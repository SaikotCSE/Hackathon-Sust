"""Alert & case management endpoints — Modules 5 + 6."""
from __future__ import annotations

import json
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session, select

from ..db import get_session
from ..models.database import Agent, Alert, Case, CashSupportRequest, ExplanationCall
from ..services.auth import Principal, current_principal, require
from ..services.cases import CaseTransition, TERMINAL_STATES, transition as do_transition
from ..services.cash_support import size_request
from ..services.notifications import add_notification
from ..services.explanations import generate_case_explanation


router = APIRouter(tags=["alerts"])


def _assert_alert_scope(principal: Principal, alert: Alert) -> None:
    """Enforce outlet/provider/owner visibility before reads or actions."""
    if principal.role == "agent" and principal.agent_id != alert.agent_id:
        raise HTTPException(403, "agents may access only their own outlet's cases")
    if principal.role == "provider" and principal.provider != alert.provider:
        raise HTTPException(403, "provider wall — not your provider's case")
    if principal.role == "risk" and not (
        alert.owner_role == "risk"
        or alert.status in ("escalated", "compliance_decision", "closed")
    ):
        raise HTTPException(403, "case has not been routed to Risk / Compliance")


def _serialize_alert(a: Alert) -> dict:
    return {
        "id": a.id,
        "agent_id": a.agent_id,
        "provider": a.provider,
        "severity": a.severity,
        "priority_score": a.priority_score,
        "title": a.title,
        "summary": a.summary,
        "reasons": json.loads(a.reasons_json or "[]"),
        "evidence": json.loads(a.evidence_json or "[]"),
        "confidence": a.confidence,
        "recommended_actions": json.loads(a.recommended_actions_json or "[]"),
        "fused_explanation": a.fused_explanation,
        "owner_role": a.owner_role,
        "owner_label": a.owner_label,
        "initial_owner": a.initial_owner,
        "status": a.status,
        "created_at": a.created_at.isoformat(),
        "updated_at": a.updated_at.isoformat(),
        "acknowledged_at": a.acknowledged_at.isoformat() if a.acknowledged_at else None,
        "resolved_at": a.resolved_at.isoformat() if a.resolved_at else None,
        "resolution_reason": a.resolution_reason,
    }


def _case_block(case: Case, session: Session | None = None) -> dict:
    notes = json.loads(case.notes_json or "[]")
    audit = json.loads(case.audit_json or "[]")
    timeline = [dict(x, kind="note") for x in notes] + [dict(x, kind="transition") for x in audit]
    timeline.sort(key=lambda x: x.get("ts", ""))
    contacts = None
    latest_explanation_call = None
    if session is not None:
        alert = session.get(Alert, case.alert_id)
        agent = session.get(Agent, alert.agent_id) if alert else None
        if agent:
            contacts = {
                "agent": {"name": agent.contact_name, "phone": agent.contact_phone, "label": agent.display_name},
                "field_officer": {"name": agent.field_officer_name, "phone": agent.field_officer_phone, "label": "Field Officer"},
                "area_manager": {"name": agent.area_manager_name, "phone": agent.area_manager_phone, "label": "Area Manager"},
                "provider_operations": {"name": f"{(alert.provider or 'Provider').upper()} Operations", "phone": "+8801600000000", "label": "Provider Operations"},
            }
        call = session.exec(
            select(ExplanationCall).where(ExplanationCall.case_id == case.id)
            .order_by(ExplanationCall.created_at.desc()).limit(1)
        ).first()
        if call:
            latest_explanation_call = {
                "id": call.id, "provider": call.provider, "model": call.model,
                "language": call.language, "endpoint": call.endpoint,
                "status": call.status, "latency_ms": call.latency_ms,
                "error": call.error, "created_at": call.created_at.isoformat(),
                "request": json.loads(call.request_json or "{}"),
                "response": json.loads(call.response_json or "{}"),
            }
    return {
        "id": case.id, "state": case.state,
        "owner_role": case.owner_role, "owner_label": case.owner_label,
        "assigned_to": case.assigned_to,
        "assigned_contact_type": case.assigned_contact_type,
        "resolution_code": case.resolution_code,
        "resolution_summary": case.resolution_summary,
        "closed_at": case.closed_at.isoformat() if case.closed_at else None,
        "risk_recommendation": case.risk_recommendation,
        "risk_recommendation_note": case.risk_recommendation_note,
        "risk_recommended_by": case.risk_recommended_by,
        "risk_recommended_at": case.risk_recommended_at.isoformat() if case.risk_recommended_at else None,
        "contacts": contacts,
        "notes": notes,
        "audit": audit,
        "timeline": timeline,
        "explanation": json.loads(case.explanation_json or "{}"),
        "explanation_provider": case.explanation_provider,
        "explanation_model": case.explanation_model,
        "explanation_status": case.explanation_status,
        "explanation_error": case.explanation_error,
        "explanation_generated_at": case.explanation_generated_at.isoformat() if case.explanation_generated_at else None,
        "explanation_language": case.explanation_language,
        "latest_explanation_call": latest_explanation_call,
    }


@router.get("/alerts")
def list_alerts(
    severity: Optional[str] = None,
    status: Optional[str] = None,
    limit: int = 50,
    principal: Principal = Depends(current_principal),
    session: Session = Depends(get_session),
):
    q = select(Alert).order_by(Alert.created_at.desc()).limit(limit)
    if severity:
        q = q.where(Alert.severity == severity)
    if status:
        q = q.where(Alert.status == status)
    alerts = session.exec(q).all()
    if principal.role == "provider" and principal.provider:
        alerts = [a for a in alerts if a.provider == principal.provider]
    elif principal.role == "agent":
        alerts = [a for a in alerts if a.agent_id == principal.agent_id]
    elif principal.role == "risk":
        alerts = [a for a in alerts if a.owner_role == "risk" and a.status in ("escalated", "risk_review")]
    case_rows = session.exec(select(Case).where(Case.alert_id.in_([a.id for a in alerts]))).all() if alerts else []
    cases_by_alert = {case.alert_id: case for case in case_rows}
    result = []
    for alert in alerts:
        item = _serialize_alert(alert)
        case = cases_by_alert.get(alert.id)
        if case is not None:
            item["case"] = _case_block(case, session)
        result.append(item)
    return {"alerts": result}


@router.get("/alerts/{alert_id}")
def get_alert(
    alert_id: int,
    principal: Principal = Depends(current_principal),
    session: Session = Depends(get_session),
):
    a = session.get(Alert, alert_id)
    if a is None:
        raise HTTPException(404, "alert not found")
    _assert_alert_scope(principal, a)
    case = session.exec(select(Case).where(Case.alert_id == a.id)).first()
    out = _serialize_alert(a)
    if case is not None:
        out["case"] = _case_block(case, session)
    return out


@router.post("/alerts/{alert_id}/notes")
def add_reviewer_note(
    alert_id: int,
    payload: dict,
    principal: Principal = Depends(current_principal),
    session: Session = Depends(get_session),
):
    a = session.get(Alert, alert_id)
    if a is None:
        raise HTTPException(404, "alert not found")
    _assert_alert_scope(principal, a)
    if principal.role not in ("agent", "ops", "risk", "provider"):
        raise HTTPException(403, "role has read-only case access")
    if principal.role == "provider" and a.initial_owner != "data-quality":
        raise HTTPException(403, "provider may add notes only to its provider-feed case")
    case = session.exec(select(Case).where(Case.alert_id == alert_id)).first()
    if case is None:
        raise HTTPException(404, "no case for this alert")
    text_value = str(payload.get("text") or "").strip()
    if not text_value:
        raise HTTPException(400, "note text is required")
    if len(text_value) > 2000:
        raise HTTPException(400, "note text must be 2000 characters or fewer")
    now_iso = datetime.utcnow().isoformat()
    notes = json.loads(case.notes_json or "[]")
    audit = json.loads(case.audit_json or "[]")
    notes.append({"ts": now_iso, "role": principal.role, "user": principal.username, "text": text_value})
    audit.append({
        "ts": now_iso, "from_state": case.state, "to_state": case.state,
        "actor": principal.username, "actor_role": principal.role,
        "reason": "reviewer_note_added", "owner_from": case.owner_role, "owner_to": case.owner_role,
    })
    case.notes_json, case.audit_json, case.updated_at = json.dumps(notes), json.dumps(audit), datetime.utcnow()
    session.add(case); session.commit(); session.refresh(case)
    return _case_block(case, session)


@router.post("/alerts/{alert_id}/risk-recommendation")
def record_risk_recommendation(
    alert_id: int,
    payload: dict,
    principal: Principal = Depends(current_principal),
    session: Session = Depends(get_session),
):
    """Persist an advisory analyst recommendation, never a final determination."""
    if principal.role != "risk":
        raise HTTPException(403, "only the assigned Risk analyst may record an investigation recommendation")
    alert = session.get(Alert, alert_id)
    if alert is None:
        raise HTTPException(404, "alert not found")
    _assert_alert_scope(principal, alert)
    case = session.exec(select(Case).where(Case.alert_id == alert_id)).first()
    if case is None:
        raise HTTPException(404, "no case for this alert")
    if case.owner_role != "risk" or case.state not in ("escalated", "risk_review"):
        raise HTTPException(409, "case was not escalated to the Risk review queue")
    recommendation = str(payload.get("recommendation") or "").strip()
    allowed = {
        "requires_human_review": "Requires Human Review",
        "requires_further_investigation": "Requires Further Investigation",
        "return_to_operations": "Return to Operations for Additional Context",
    }
    if recommendation not in allowed:
        raise HTTPException(400, "invalid advisory recommendation")
    comment = str(payload.get("comment") or "").strip()
    if not comment:
        raise HTTPException(400, "an investigation comment is required")
    if len(comment) > 2000:
        raise HTTPException(400, "comment must be 2000 characters or fewer")
    now = datetime.utcnow()
    label = allowed[recommendation]
    previous_state = case.state
    case.state = "risk_review"
    case.risk_recommendation = recommendation
    case.risk_recommendation_note = comment
    case.risk_recommended_by = principal.username
    case.risk_recommended_at = now
    case.updated_at = now
    notes = json.loads(case.notes_json or "[]")
    audit = json.loads(case.audit_json or "[]")
    notes.append({"ts": now.isoformat(), "role": "risk", "user": principal.username, "text": f"{label}: {comment}"})
    audit.append({
        "ts": now.isoformat(), "from_state": previous_state, "to_state": "risk_review",
        "actor": principal.username, "actor_role": "risk",
        "reason": f"advisory_recommendation:{recommendation}: {comment}",
        "owner_from": "risk", "owner_to": "risk",
    })
    case.notes_json, case.audit_json = json.dumps(notes), json.dumps(audit)
    alert.status = "risk_review"
    alert.updated_at = now
    session.add(case); session.add(alert)
    add_notification(
        session, alert=alert, case=case, recipient_role="ops",
        event="risk_advisory_recommendation",
        message=f"Case #{case.id}: {label}. {comment}", actor=principal.username,
    )
    add_notification(
        session, alert=alert, case=case, recipient_role="agent", recipient_agent_id=alert.agent_id,
        event="risk_review_updated",
        message=f"Case #{case.id} remains under human review. Advisory status: {label}.", actor=principal.username,
    )
    session.commit(); session.refresh(case)
    return _case_block(case, session)


@router.post("/alerts/{alert_id}/coordination")
def coordinate_case(
    alert_id: int,
    payload: dict,
    principal: Principal = Depends(current_principal),
    session: Session = Depends(get_session),
):
    """Record a safe Operations coordination action; never changes balances."""
    if principal.role != "ops":
        raise HTTPException(403, "only Provider Operations / Network Coordination may coordinate this case")
    alert = session.get(Alert, alert_id)
    if alert is None:
        raise HTTPException(404, "alert not found")
    _assert_alert_scope(principal, alert)
    case = session.exec(select(Case).where(Case.alert_id == alert_id)).first()
    if case is None:
        raise HTTPException(404, "no case for this alert")
    if case.owner_role != "ops" or case.state in ("escalated", "compliance_decision", "closed"):
        raise HTTPException(409, "case is no longer in the Operations coordination queue")

    action = str(payload.get("action") or "").strip()
    comment = str(payload.get("comment") or "").strip()
    if not comment:
        raise HTTPException(400, "a coordination comment is required")
    if len(comment) > 2000:
        raise HTTPException(400, "comment must be 2000 characters or fewer")
    supported = {
        "notify_agent", "notify_field_officer", "request_field_verification",
        "request_agent_confirmation", "notify_area_manager",
        "request_provider_operations_support", "assign", "reassign",
    }
    if action not in supported:
        raise HTTPException(400, "unsupported coordination action")

    agent = session.get(Agent, alert.agent_id)
    now = datetime.utcnow()
    recipient_role = "ops"
    recipient_agent_id = None
    event_label = action.replace("_", " ")

    if action in ("notify_agent", "request_agent_confirmation"):
        recipient_role, recipient_agent_id = "agent", alert.agent_id
    elif action == "request_provider_operations_support":
        recipient_role = "provider"
    elif action in ("assign", "reassign"):
        target = str(payload.get("target") or "").strip()
        targets = {
            "field_officer": agent.field_officer_name if agent else "Field Officer",
            "area_manager": agent.area_manager_name if agent else "Area Manager",
            "operations": "Provider Operations queue",
        }
        if target not in targets:
            raise HTTPException(400, "target must be field_officer, area_manager, or operations")
        previous = case.assigned_to
        case.assigned_to = targets[target]
        case.assigned_contact_type = target
        event_label = f"{action} from {previous} to {case.assigned_to}"

    message = f"Case #{case.id}: {event_label}. {comment}"
    notes = json.loads(case.notes_json or "[]")
    audit = json.loads(case.audit_json or "[]")
    notes.append({"ts": now.isoformat(), "role": principal.role, "user": principal.username, "text": message})
    audit.append({
        "ts": now.isoformat(), "from_state": case.state, "to_state": case.state,
        "actor": principal.username, "actor_role": principal.role,
        "reason": f"coordination:{action}: {comment}",
        "owner_from": case.owner_role, "owner_to": case.owner_role,
    })
    case.notes_json, case.audit_json, case.updated_at = json.dumps(notes), json.dumps(audit), now
    session.add(case)
    add_notification(
        session, alert=alert, case=case, recipient_role=recipient_role,
        recipient_agent_id=recipient_agent_id, event=f"coordination_{action}",
        message=message, actor=principal.username,
    )
    session.commit(); session.refresh(case)
    return _case_block(case, session)


@router.post("/alerts/{alert_id}/explanation/regenerate")
def regenerate_explanation(
    alert_id: int,
    payload: dict,
    principal: Principal = Depends(current_principal),
    session: Session = Depends(get_session),
):
    a = session.get(Alert, alert_id)
    if a is None:
        raise HTTPException(404, "alert not found")
    _assert_alert_scope(principal, a)
    if principal.role not in ("agent", "ops", "risk", "provider"):
        raise HTTPException(403, "only the assigned review team may regenerate an explanation")
    if principal.role == "provider" and a.initial_owner != "data-quality":
        raise HTTPException(403, "provider may regenerate only its provider-feed explanation")
    case = session.exec(select(Case).where(Case.alert_id == alert_id)).first()
    if case is None:
        raise HTTPException(404, "no case for this alert")
    language = str(payload.get("language") or "en")
    try:
        generate_case_explanation(session, case, a, language=language)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return _case_block(case, session)


@router.post("/alerts/{alert_id}/transition")
def transition_case(
    alert_id: int,
    payload: dict,
    principal: Principal = Depends(current_principal),
    session: Session = Depends(get_session),
):
    require(principal, "can_act_on_alerts")
    a = session.get(Alert, alert_id)
    if a is None:
        raise HTTPException(404, "alert not found")
    _assert_alert_scope(principal, a)
    case = session.exec(select(Case).where(Case.alert_id == a.id)).first()
    if case is None:
        raise HTTPException(404, "no case for this alert")
    action = payload.get("action")
    note = payload.get("note")
    if action not in ("ack", "review", "start", "resolve", "escalate", "close"):
        raise HTTPException(400, "invalid action")
    if not (note or "").strip():
        raise HTTPException(400, f"a comment is required to {action} a case")

    # RBAC strict enforcement on the state-machine actions. This is the
    # ground truth — the UI hides the buttons it can't fire, but if a user
    # hand-crafts a request we still reject it.
    if action == "close" and not (
        (principal.role == "ops" and case.state == "resolved")
        or (principal.role == "provider" and a.initial_owner == "data-quality" and case.state == "resolved")
    ):
        raise HTTPException(403, "case closure is restricted to the responsible operational team after resolution")
    if principal.role == "agent" and action != "ack":
        raise HTTPException(403, "agents may acknowledge their own alert; Operations coordinates later case steps")
    if principal.role == "ops" and action not in ("ack", "review", "start", "resolve", "escalate", "close"):
        raise HTTPException(403, "Operations cannot perform that case action")
    if principal.role == "risk":
        raise HTTPException(403, "Risk analysts may add notes and advisory recommendations only")
    if principal.role == "provider" and not (
        a.initial_owner == "data-quality" and action in ("ack", "review", "start", "resolve", "close")
    ):
        raise HTTPException(403, "providers may act only on their own provider-feed case")
    if principal.role not in ("agent", "ops", "risk", "provider"):
        raise HTTPException(403, f"{principal.role} has read-only case access")

    try:
        case = do_transition(session, CaseTransition(
            case_id=case.id, action=action,
            actor_role=principal.role, actor_user=principal.username,
            note=note,
        ))
    except ValueError as e:
        # State-machine violations are client errors (409 Conflict), not
        # server faults. The brief case-flow is the source of truth — UI
        # buttons respect it, but a hand-crafted request should also get
        # a clean HTTP code, not a 500.
        raise HTTPException(409, str(e))
    a = session.get(Alert, alert_id)
    out = _serialize_alert(a)
    out["case"] = _case_block(case, session)
    return out


# ---------------------------------------------------------------------------
# Recommended Action execution endpoint
# ---------------------------------------------------------------------------
# The Decision Intelligence panel surfaces six recommended actions drawn from
# config/decision-weights.json (notify_ops, assign_field_officer,
# request_cash_support, monitor, risk_review, data_quality_followup). This
# endpoint lets the UI fire any of them against an Alert without having to
# know the case-state-machine vocabulary. Two actions (ack/escalate) wrap
# existing state transitions; one (monitor) is audit-only and never
# transitions; the rest are audit + transition.
#
# RBAC: server-side enforcement below MUST stay in sync with
# apps/web/lib/rbac.ts:RECOMMENDED_ACTION_ALLOWED.

_RECOMMENDED_ACTIONS: dict = {
    # High-level verb the UI shows → (allowed_roles, transition_or_None, default_note)
    "notify_ops":              (("agent", "ops"),               None,       "Notify Operations: hand-off to Provider Operations for triage."),
    "assign_field_officer":    (("ops",),                        "ack",      "Assign Field Officer: dispatched to verify service readiness."),
    "request_cash_support":    (("agent", "ops"),               None,       "Request Cash Support: sent to the financial service provider for approved coordination."),
    "monitor":                 (("agent", "ops", "risk", "provider"), None, "Monitor: kept watch — no immediate action."),
    "risk_review":             (("ops",),                        "escalate", "Risk Review: forwarded to Risk / Compliance for independent review."),
    "data_quality_followup":   (("provider", "ops"),            "review",   "Follow up with provider feed: feed degraded — chasing the integration team."),
}


@router.post("/alerts/{alert_id}/action")
def execute_recommended_action(
    alert_id: int,
    payload: dict,
    principal: Principal = Depends(current_principal),
    session: Session = Depends(get_session),
):
    """Fire a recommended action from the Decision Intelligence panel.

    Body: { "action_key": "<one of the six keys above>", "note": "<optional override>" }
    The endpoint enforces RBAC server-side and returns the updated alert
    + case in the same shape as `/transition`.
    """
    action_key = (payload.get("action_key") or "").strip()
    custom_note = payload.get("note")

    if action_key not in _RECOMMENDED_ACTIONS:
        raise HTTPException(400, f"unknown recommended action: {action_key!r}")

    allowed_roles, transition_action, default_note = _RECOMMENDED_ACTIONS[action_key]
    if principal.role not in allowed_roles:
        raise HTTPException(
            403,
            f"{principal.role} cannot perform recommended action '{action_key}' "
            f"(allowed: {', '.join(allowed_roles)}).",
        )

    a = session.get(Alert, alert_id)
    if a is None:
        raise HTTPException(404, "alert not found")
    _assert_alert_scope(principal, a)
    case = session.exec(select(Case).where(Case.alert_id == a.id)).first()
    if case is None:
        raise HTTPException(404, "no case for this alert")
    if case.state in TERMINAL_STATES:
        raise HTTPException(409, f"case is {case.state}; final state is immutable")

    note_text = (custom_note or "").strip() or default_note

    if action_key == "notify_ops":
        add_notification(
            session, alert=a, case=case, recipient_role="ops",
            event="operations_notified", message=f"Case #{case.id}: {note_text}",
            actor=principal.username,
        )
        notes = json.loads(case.notes_json or "[]")
        audit = json.loads(case.audit_json or "[]")
        now_iso = datetime.utcnow().isoformat()
        notes.append({"ts": now_iso, "role": principal.role, "user": principal.username, "text": note_text})
        audit.append({"ts": now_iso, "from_state": case.state, "to_state": case.state,
                      "actor": principal.username, "actor_role": principal.role,
                      "reason": "operations_notified", "owner_from": case.owner_role, "owner_to": case.owner_role})
        case.notes_json, case.audit_json, case.updated_at = json.dumps(notes), json.dumps(audit), datetime.utcnow()
        session.add(case); session.commit()
        out = _serialize_alert(a)
        out["case"] = _case_block(case, session)
        out["executed_action"] = action_key
        return out

    if action_key == "request_cash_support":
        if not a.provider or a.provider == "physical":
            raise HTTPException(400, "cash support must target a financial service provider")
        if principal.role == "agent" and principal.agent_id != a.agent_id:
            raise HTTPException(403, "agents may request support only for their own outlet")
        active = session.exec(
            select(CashSupportRequest)
            .where(CashSupportRequest.alert_id == a.id)
            .where(CashSupportRequest.status.in_(["requested", "acknowledged", "approved"]))
            .order_by(CashSupportRequest.created_at.desc())
        ).first()
        if active is None:
            active = CashSupportRequest(
                alert_id=a.id, agent_id=a.agent_id, provider=a.provider,
                requested_by=principal.username, note=note_text,
            )
            session.add(active)
        try:
            size_request(session, active, override_amount=payload.get("amount"))
        except ValueError as exc:
            raise HTTPException(409, str(exc))
        if not active.amount or active.amount <= 0:
            raise HTTPException(409, "forecast indicates no additional provider support is currently required")
        note_text = f"{note_text} Requested {active.amount:,.0f} BDT. {active.calculation}"
        notes = json.loads(case.notes_json or "[]")
        audit = json.loads(case.audit_json or "[]")
        now_iso = datetime.utcnow().isoformat()
        notes.append({"ts": now_iso, "role": principal.role, "user": principal.username, "text": note_text})
        audit.append({"ts": now_iso, "from_state": case.state, "to_state": case.state,
                      "actor": principal.username, "actor_role": principal.role,
                      "reason": "cash_support_requested_from_provider",
                      "owner_from": case.owner_role, "owner_to": case.owner_role})
        case.notes_json, case.audit_json, case.updated_at = json.dumps(notes), json.dumps(audit), datetime.utcnow()
        session.add(case)
        session.commit()
        session.refresh(active)
        out = _serialize_alert(a)
        out["executed_action"] = action_key
        out["cash_support_request_id"] = active.id
        out["cash_support_status"] = active.status
        out["cash_support_amount"] = active.amount
        out["cash_support_calculation"] = active.calculation
        return out

    if transition_action == "ack":
        # `notify_ops` is semantically "ops was informed", not literally an
        # `ack` state-machine move. If the case is in a state where ack is
        # legal (currently `assigned`), apply the transition; otherwise drop
        # a note + audit line and leave the state alone. This keeps the
        # button working even after Module 4's escalation engine has
        # auto-rerouted the case to `escalated` — the user has still
        # actually notified ops, and we shouldn't silently fail.
        ack_legal = (case.state, "ack") in {
            ("assigned", "ack"),
        }
        if not ack_legal:
            notes = json.loads(case.notes_json or "[]")
            audit = json.loads(case.audit_json or "[]")
            now_iso = datetime.utcnow().isoformat()
            notes.append({
                "ts": now_iso, "role": principal.role, "user": principal.username,
                "text": note_text,
            })
            audit.append({
                "ts": now_iso, "from_state": case.state, "to_state": case.state,
                "actor": principal.username, "actor_role": principal.role,
                "reason": f"recommended_action:{action_key} (audit-only — already {case.state})",
                "owner_from": case.owner_role, "owner_to": case.owner_role,
            })
            case.notes_json = json.dumps(notes)
            case.audit_json = json.dumps(audit)
            case.updated_at = datetime.utcnow()
            session.add(case)
            session.commit()
            session.refresh(case)
            a = session.get(Alert, alert_id)
            out = _serialize_alert(a)
            out["case"] = _case_block(case, session)
            out["executed_action"] = action_key
            return out
    if transition_action is None:
        # "monitor" is audit-only — append a note + audit line without changing state.
        # This preserves the case state-machine integrity: we never auto-change
        # state from an action that doesn't have a defined transition.
        notes = json.loads(case.notes_json or "[]")
        audit = json.loads(case.audit_json or "[]")
        now_iso = datetime.utcnow().isoformat()
        notes.append({
            "ts": now_iso, "role": principal.role, "user": principal.username,
            "text": note_text,
        })
        audit.append({
            "ts": now_iso, "from_state": case.state, "to_state": case.state,
            "actor": principal.username, "actor_role": principal.role,
            "reason": f"recommended_action:{action_key}",
            "owner_from": case.owner_role, "owner_to": case.owner_role,
        })
        case.notes_json = json.dumps(notes)
        case.audit_json = json.dumps(audit)
        case.updated_at = datetime.utcnow()
        session.add(case)
        session.commit()
        session.refresh(case)
    else:
        # "ack"/"review"/"escalate" wrap an existing state-machine transition.
        # Use do_transition so the same audit + alert-side effects happen.
        try:
            case = do_transition(session, CaseTransition(
                case_id=case.id, action=transition_action,
                actor_role=principal.role, actor_user=principal.username,
                note=note_text,
            ))
        except ValueError as e:
            raise HTTPException(409, str(e))

    a = session.get(Alert, alert_id)
    out = _serialize_alert(a)
    out["case"] = _case_block(case, session)
    out["executed_action"] = action_key
    return out
