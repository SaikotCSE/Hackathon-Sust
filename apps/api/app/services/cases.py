"""Module 6 — Case Management & Escalation Workflow.

Implements the state machine from the brief as a real, enforced flow:

    Alert Generated
          ↓
    Assigned to Field Officer
          ↓
    Acknowledged
          ↓
    Under Operational Review (Provider Operations / Network Coordination)
          ↓
    Resolved  ──OR──  Escalated to Risk Team → Compliance Decision → Closed

Initial ownership is set by Module 4's Ownership Engine. Every state transition
is initiated by an authorized human through the API.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

from sqlmodel import Session, select

from ..models.database import Agent, Alert, Case
from .notifications import add_notification


TRANSITIONS: Dict[Tuple[str, str], str] = {
    # forward path
    ("assigned", "ack"): "acknowledged",
    ("acknowledged", "review"): "review",
    ("review", "start"): "in_progress",
    ("in_progress", "resolve"): "resolved",
    ("resolved", "close"): "closed",
    # escalation path
    ("assigned", "escalate"): "escalated",
    ("acknowledged", "escalate"): "escalated",
    ("review", "escalate"): "escalated",
    ("in_progress", "escalate"): "escalated",
}

TERMINAL_STATES = {"closed"}


@dataclass
class CaseTransition:
    case_id: int
    action: str  # ack | review | start | resolve | escalate | close (operations only)
    actor_role: str
    actor_user: str
    note: Optional[str] = None


def open_case_for_alert(
    session: Session, alert: Alert, *, owner_role: str, owner_label: str
) -> Case:
    """Create a Case in the 'assigned' state — fired when an alert is first promoted
    through Module 5's tiered-alert rendering pipeline."""
    existing = session.exec(select(Case).where(Case.alert_id == alert.id)).first()
    if existing is not None:
        return existing
    agent = session.get(Agent, alert.agent_id)
    assigned_to = agent.field_officer_name if agent and owner_role == "ops" else owner_label
    opened_at = datetime.utcnow().isoformat()
    case = Case(
        alert_id=alert.id,
        state="assigned",
        owner_role=owner_role,
        owner_label=owner_label,
        assigned_to=assigned_to,
        assigned_contact_type="field_officer" if owner_role == "ops" else owner_role,
        notes_json=json.dumps([
            {"ts": opened_at, "role": owner_role, "user": "system",
             "text": f"Persistent case opened and routed to {owner_label}."}
        ]),
        audit_json=json.dumps([
            {"ts": opened_at, "from_state": None, "to_state": "new",
             "actor": "system", "actor_role": "system", "reason": "alert promoted to operations case",
             "owner_from": None, "owner_to": None},
            {"ts": opened_at, "from_state": "new", "to_state": "assigned",
             "actor": "system", "actor_role": "system", "reason": "routed by DSS Ownership Engine",
             "owner_from": None, "owner_to": owner_role}
        ]),
    )
    session.add(case)
    session.commit()
    session.refresh(case)
    from .explanations import generate_case_explanation
    generate_case_explanation(session, case, alert)
    add_notification(
        session, alert=alert, case=case, recipient_role=owner_role,
        recipient_agent_id=alert.agent_id if owner_role == "agent" else None,
        event="case_assigned",
        message=f"Case #{case.id} was routed to {owner_label}. Recommended action is ready for review.",
        actor="dss-ownership-engine",
    )
    # The originating agent can track where its alert was routed.
    if owner_role != "agent":
        add_notification(
            session, alert=alert, case=case, recipient_role="agent",
            recipient_agent_id=alert.agent_id, event="case_routed",
            message=f"Case #{case.id} was routed to {owner_label}.", actor="dss-ownership-engine",
        )
    if alert.severity == "critical":
        add_notification(
            session, alert=alert, case=case, recipient_role="management",
            event="critical_case_visibility",
            message=f"Critical case #{case.id} opened for management visibility; Operations retains action ownership.",
            actor="dss-ownership-engine",
        )
    session.commit()
    return case


def process_due_escalations(session: Session, *, now: Optional[datetime] = None) -> int:
    """Escalate overdue, unacknowledged high-impact cases.

    Critical cases have a 10-minute acknowledgement SLA and high cases a
    30-minute SLA. Low cases never auto-escalate. This only routes and notifies;
    it cannot resolve a case or make a compliance decision.
    """
    now = now or datetime.utcnow()
    cases = session.exec(select(Case).where(Case.state == "assigned")).all()
    escalated = 0
    for case in cases:
        alert = session.get(Alert, case.alert_id)
        if alert is None or alert.owner_role == "provider":
            continue
        minutes = 10 if alert.severity == "critical" else 30 if alert.severity == "high" else None
        if minutes is None or case.created_at > now - timedelta(minutes=minutes):
            continue
        transition(session, CaseTransition(
            case_id=case.id,
            action="escalate",
            actor_role="system",
            actor_user="escalation-engine",
            note=f"Unacknowledged {alert.severity} case exceeded the {minutes}-minute acknowledgement SLA.",
        ))
        escalated += 1
    return escalated


def normalize_open_case_ownership(session: Session) -> int:
    """Repair legacy anomaly cases that bypassed Operations triage."""
    alerts = session.exec(
        select(Alert)
        .where(Alert.initial_owner == "anomaly")
        .where(Alert.status.in_(["open", "assigned", "acknowledged", "under_review", "in_progress"]))
    ).all()
    changed = 0
    for alert in alerts:
        case = session.exec(select(Case).where(Case.alert_id == alert.id)).first()
        if case is None or case.state in ("escalated", "compliance_decision", "closed", "resolved"):
            continue
        label = "Provider Operations / Network Coordination — initial triage"
        if alert.owner_role != "ops" or case.owner_role != "ops":
            alert.owner_role = case.owner_role = "ops"
            alert.owner_label = case.owner_label = label
            session.add(alert); session.add(case)
            changed += 1
    if changed:
        session.commit()
    return changed


def transition(session: Session, t: CaseTransition) -> Case:
    case = session.get(Case, t.case_id)
    if case is None:
        raise ValueError(f"Case {t.case_id} not found")
    if case.state in TERMINAL_STATES:
        raise ValueError(f"Case {case.id} is {case.state}; final state is immutable")
    key = (case.state, t.action)
    if key not in TRANSITIONS:
        raise ValueError(f"Illegal transition: {case.state} --{t.action}-->?")

    new_state = TRANSITIONS[key]
    notes = json.loads(case.notes_json or "[]")
    audit = json.loads(case.audit_json or "[]")
    now = datetime.utcnow().isoformat()
    notes.append({"ts": now, "role": t.actor_role, "user": t.actor_user,
                  "text": t.note or f"transition: {t.action}"})
    owner_from = case.owner_role
    owner_to = "risk" if new_state in ("escalated", "compliance_decision") else owner_from
    audit.append({"ts": now, "from_state": case.state, "to_state": new_state,
                  "actor": t.actor_user, "actor_role": t.actor_role,
                  "reason": t.note or t.action, "owner_from": owner_from, "owner_to": owner_to})
    case.state = new_state
    case.notes_json = json.dumps(notes)
    case.audit_json = json.dumps(audit)
    case.updated_at = datetime.utcnow()
    if owner_to == "risk":
        case.owner_role = "risk"
        case.owner_label = "Risk / Compliance analyst — human review"
        case.assigned_to = "Risk / Compliance review queue"
        case.assigned_contact_type = "risk"
    if new_state == "resolved":
        case.resolution_code = "operationally_resolved"
        case.resolution_summary = t.note
    if new_state == "closed":
        case.closed_at = datetime.utcnow()

    # Reflect on the alert too
    alert = session.get(Alert, case.alert_id)
    if alert is not None:
        alert.status = {
            "assigned": "assigned",
            "acknowledged": "acknowledged",
            "review": "under_review",
            "in_progress": "in_progress",
            "resolved": "resolved",
            "escalated": "escalated",
            "compliance_decision": "compliance_decision",
            "closed": "closed",
        }.get(new_state, alert.status)
        alert.updated_at = datetime.utcnow()
        alert.owner_role = case.owner_role
        alert.owner_label = case.owner_label
        if new_state == "acknowledged" and alert.acknowledged_at is None:
            alert.acknowledged_at = datetime.utcnow()
        if new_state in ("resolved", "closed"):
            alert.resolved_at = datetime.utcnow()
            alert.resolution_reason = t.note or f"{new_state} by {t.actor_user}"

        if new_state == "escalated":
            add_notification(
                session, alert=alert, case=case, recipient_role="risk",
                event="case_escalated", message=f"Case #{case.id} requires Risk / Compliance review: {t.note}",
                actor=t.actor_user,
            )
        elif new_state in ("acknowledged", "review", "in_progress"):
            add_notification(
                session, alert=alert, case=case, recipient_role="agent",
                recipient_agent_id=alert.agent_id, event=f"case_{new_state}",
                message=f"{case.owner_label} moved case #{case.id} to {new_state}: {t.note or t.action}",
                actor=t.actor_user,
            )
        elif new_state in ("resolved", "compliance_decision", "closed"):
            add_notification(
                session, alert=alert, case=case, recipient_role="agent",
                recipient_agent_id=alert.agent_id, event=f"case_{new_state}",
                message=f"Case #{case.id} is now {new_state}: {t.note or t.action}",
                actor=t.actor_user,
            )

    session.add(case)
    if alert is not None:
        session.add(alert)
    session.commit()
    session.refresh(case)
    return case
