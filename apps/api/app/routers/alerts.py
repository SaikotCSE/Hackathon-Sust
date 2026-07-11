"""Alert & case management endpoints — Modules 5 + 6."""
from __future__ import annotations

import json
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session, select

from ..db import get_session
from ..models.database import Alert, Case
from ..services.auth import Principal, current_principal, require
from ..services.cases import CaseTransition, transition as do_transition


router = APIRouter(tags=["alerts"])


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
    # provider-role wall
    if principal.role == "provider" and principal.provider:
        alerts = [a for a in alerts if a.provider == principal.provider]
    return {"alerts": [_serialize_alert(a) for a in alerts]}


@router.get("/alerts/{alert_id}")
def get_alert(
    alert_id: int,
    principal: Principal = Depends(current_principal),
    session: Session = Depends(get_session),
):
    a = session.get(Alert, alert_id)
    if a is None:
        raise HTTPException(404, "alert not found")
    if principal.role == "provider" and principal.provider and a.provider != principal.provider:
        raise HTTPException(403, "provider wall — not your provider's alert")
    case = session.exec(select(Case).where(Case.alert_id == a.id)).first()
    out = _serialize_alert(a)
    if case is not None:
        out["case"] = {
            "id": case.id,
            "state": case.state,
            "owner_role": case.owner_role,
            "owner_label": case.owner_label,
            "notes": json.loads(case.notes_json or "[]"),
            "audit": json.loads(case.audit_json or "[]"),
        }
    return out


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
    case = session.exec(select(Case).where(Case.alert_id == a.id)).first()
    if case is None:
        raise HTTPException(404, "no case for this alert")
    action = payload.get("action")
    note = payload.get("note")
    if action not in ("ack", "review", "resolve", "escalate", "decision", "close"):
        raise HTTPException(400, "invalid action")

    # RBAC strict enforcement on the state-machine actions. This is the
    # ground truth — the UI hides the buttons it can't fire, but if a user
    # hand-crafts a request we still reject it.
    if action == "close" and principal.role != "risk":
        raise HTTPException(403, "Only Risk/Compliance may close a case.")
    if action == "decision" and principal.role != "risk":
        raise HTTPException(403, "Only Risk/Compliance may issue a compliance decision.")
    if action in ("ack", "review", "escalate", "resolve") and principal.role not in ("agent", "ops", "risk"):
        raise HTTPException(403, f"{principal.role} cannot {action} alerts.")

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
    out["case"] = {
        "id": case.id,
        "state": case.state,
        "notes": json.loads(case.notes_json or "[]"),
        "audit": json.loads(case.audit_json or "[]"),
    }
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
    "notify_ops":              (("agent", "ops", "risk"),      "ack",      "Notify Operations: hand-off to Provider Operations for triage."),
    "assign_field_officer":    (("ops", "risk"),               "ack",      "Assign Field Officer: dispatched to top up / verify stock."),
    "request_cash_support":    (("ops", "risk"),               "review",   "Request Cash Support: opened a cash-support ticket with the provider."),
    "monitor":                 (("agent", "ops", "risk", "provider", "management"), None, "Monitor: kept watch — no immediate action."),
    "risk_review":             (("agent", "ops", "risk"),      "escalate", "Risk Review: forwarded to Risk / Compliance for final ruling."),
    "data_quality_followup":   (("provider", "ops", "risk"),   "review",   "Follow up with provider feed: feed degraded — chasing the integration team."),
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
    case = session.exec(select(Case).where(Case.alert_id == a.id)).first()
    if case is None:
        raise HTTPException(404, "no case for this alert")

    note_text = (custom_note or "").strip() or default_note

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
                "actor": principal.username, "reason": f"recommended_action:{action_key} (audit-only — already {case.state})",
            })
            case.notes_json = json.dumps(notes)
            case.audit_json = json.dumps(audit)
            case.updated_at = datetime.utcnow()
            session.add(case)
            session.commit()
            session.refresh(case)
            a = session.get(Alert, alert_id)
            out = _serialize_alert(a)
            out["case"] = {
                "id": case.id,
                "state": case.state,
                "owner_role": case.owner_role,
                "owner_label": case.owner_label,
                "notes": json.loads(case.notes_json or "[]"),
                "audit": json.loads(case.audit_json or "[]"),
            }
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
            "actor": principal.username, "reason": f"recommended_action:{action_key}",
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
    out["case"] = {
        "id": case.id,
        "state": case.state,
        "owner_role": case.owner_role,
        "owner_label": case.owner_label,
        "notes": json.loads(case.notes_json or "[]"),
        "audit": json.loads(case.audit_json or "[]"),
    }
    out["executed_action"] = action_key
    return out