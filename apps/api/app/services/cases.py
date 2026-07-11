"""Module 6 — Case Management & Escalation Workflow.

Implements the state machine from the brief as a real, enforced flow:

    Alert Generated
          ↓
    Assigned to Field Officer
          ↓
    Acknowledged
          ↓
    Area Manager Review
          ↓
    Resolved  ──OR──  Escalated to Risk Team → Compliance Decision → Closed

Initial ownership is set by Module 4's Ownership Engine; auto-escalation is
driven by Module 4's Escalation Engine timers.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

from sqlmodel import Session, select

from ..models.database import Alert, Case


TRANSITIONS: Dict[Tuple[str, str], str] = {
    # forward path
    ("assigned", "ack"): "acknowledged",
    ("acknowledged", "review"): "review",
    ("review", "resolve"): "resolved",
    # escalation path
    ("assigned", "escalate"): "escalated",
    ("acknowledged", "escalate"): "escalated",
    ("review", "escalate"): "escalated",
    ("escalated", "decision"): "compliance_decision",
    ("compliance_decision", "close"): "closed",
    # resolution can come from anywhere downstream of review
    ("escalated", "resolve"): "resolved",
    ("compliance_decision", "resolve"): "resolved",
}

TERMINAL_STATES = {"resolved", "closed"}


@dataclass
class CaseTransition:
    case_id: int
    action: str  # ack | review | resolve | escalate | decision | close
    actor_role: str
    actor_user: str
    note: Optional[str] = None


def open_case_for_alert(
    session: Session, alert: Alert, *, owner_role: str, owner_label: str
) -> Case:
    """Create a Case in the 'assigned' state — fired when an alert is first promoted
    through Module 5's tiered-alert rendering pipeline."""
    case = Case(
        alert_id=alert.id,
        state="assigned",
        owner_role=owner_role,
        owner_label=owner_label,
        notes_json=json.dumps([
            {"ts": datetime.utcnow().isoformat(), "role": owner_role, "user": "system",
             "text": "Case opened and assigned to field officer tier."}
        ]),
        audit_json=json.dumps([
            {"ts": datetime.utcnow().isoformat(), "from_state": None, "to_state": "assigned",
             "actor": "system", "reason": "auto-assigned by Ownership Engine"}
        ]),
    )
    session.add(case)
    session.commit()
    session.refresh(case)
    return case


def transition(session: Session, t: CaseTransition) -> Case:
    case = session.get(Case, t.case_id)
    if case is None:
        raise ValueError(f"Case {t.case_id} not found")
    key = (case.state, t.action)
    if key not in TRANSITIONS:
        raise ValueError(f"Illegal transition: {case.state} --{t.action}-->?")

    new_state = TRANSITIONS[key]
    notes = json.loads(case.notes_json or "[]")
    audit = json.loads(case.audit_json or "[]")
    now = datetime.utcnow().isoformat()
    notes.append({"ts": now, "role": t.actor_role, "user": t.actor_user,
                  "text": t.note or f"transition: {t.action}"})
    audit.append({"ts": now, "from_state": case.state, "to_state": new_state,
                  "actor": t.actor_user, "reason": t.action})
    case.state = new_state
    case.notes_json = json.dumps(notes)
    case.audit_json = json.dumps(audit)
    case.updated_at = datetime.utcnow()

    # Reflect on the alert too
    alert = session.get(Alert, case.alert_id)
    if alert is not None:
        alert.status = {
            "assigned": "assigned",
            "acknowledged": "acknowledged",
            "review": "under_review",
            "resolved": "resolved",
            "escalated": "escalated",
            "compliance_decision": "compliance_decision",
            "closed": "closed",
        }.get(new_state, alert.status)
        alert.updated_at = datetime.utcnow()
        if new_state == "acknowledged" and alert.acknowledged_at is None:
            alert.acknowledged_at = datetime.utcnow()
        if new_state == "resolved":
            alert.resolved_at = datetime.utcnow()
            alert.resolution_reason = t.note or "resolved by operations"

    session.add(case)
    if alert is not None:
        session.add(alert)
    session.commit()
    session.refresh(case)
    return case


# ---------------------------------------------------------------------------
# Module 4 — Escalation Engine driver
# ---------------------------------------------------------------------------

def auto_escalate_due(session: Session, *, escalation_cfg: dict) -> List[Case]:
    """Re-route unacknowledged alerts whose SLA timer has elapsed.

    SAFETY: this function performs *routing only*. It MUST NOT resolve or close
    a case, and MUST NOT decide an outcome on a customer — the final compliance
    ruling is the analyst's. The only state-machine transition this can fire
    is `escalated` (re-routing from `open` to `escalated` after the SLA
    timer). Anything that changes a terminal state (`resolved`, `closed`,
    `compliance_decision`) belongs to a human via POST /alerts/{id}/transition.
    """
    now = datetime.utcnow()
    crit = (escalation_cfg or {}).get("critical_minutes") or 10
    high = (escalation_cfg or {}).get("high_minutes") or 30

    open_alerts = session.exec(
        select(Alert).where(Alert.status == "open").where(Alert.acknowledged_at == None)  # noqa: E711
    ).all()
    escalated: List[Case] = []
    for a in open_alerts:
        threshold = crit if a.severity == "critical" else (high if a.severity == "high" else None)
        if threshold is None:
            continue
        if (now - a.created_at).total_seconds() < threshold * 60:
            continue
        case = session.exec(select(Case).where(Case.alert_id == a.id)).first()
        if case is None:
            case = open_case_for_alert(session, a, owner_role=a.owner_role, owner_label=a.owner_label)
        try:
            transition(session, CaseTransition(
                case_id=case.id,
                # Hard-coded to "escalate" — re-routing only.
                # Do not add other actions here; see the docstring above.
                action="escalate",
                actor_role="system",
                actor_user="escalation-engine",
                note=(
                    f"auto-routed to risk queue after {threshold} min "
                    f"unacknowledged ({a.severity} tier). Routing-only — "
                    f"does not decide an outcome."
                ),
            ))
            escalated.append(case)
        except ValueError:
            continue
    return escalated