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

    case = do_transition(session, CaseTransition(
        case_id=case.id, action=action,
        actor_role=principal.role, actor_user=principal.username,
        note=note,
    ))
    a = session.get(Alert, alert_id)
    out = _serialize_alert(a)
    out["case"] = {
        "id": case.id,
        "state": case.state,
        "notes": json.loads(case.notes_json or "[]"),
        "audit": json.loads(case.audit_json or "[]"),
    }
    return out