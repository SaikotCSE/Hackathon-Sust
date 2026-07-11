"""Agent → financial-service-provider cash-support workflow."""
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session, select

from ..db import get_session
from ..models.database import Agent, Alert, Case, CashSupportRequest
from ..services.auth import Principal, current_principal
from ..services.cash_support import size_request
from ..services.notifications import add_notification
import json

router = APIRouter(prefix="/cash-support", tags=["cash-support"])


def serialize(row: CashSupportRequest, session: Session) -> dict:
    agent = session.get(Agent, row.agent_id)
    return {
        "id": row.id, "alert_id": row.alert_id, "agent_id": row.agent_id,
        "agent_code": agent.code if agent else str(row.agent_id),
        "agent_name": agent.display_name if agent else "Unknown agent",
        "provider": row.provider, "requested_by": row.requested_by,
        "amount": row.amount, "note": row.note, "status": row.status,
        "forecast_balance": row.forecast_balance,
        "forecast_burn_rate_per_min": row.forecast_burn_rate_per_min,
        "coverage_hours": row.coverage_hours, "target_balance": row.target_balance,
        "calculation": row.calculation, "applied_amount": row.applied_amount,
        "balance_after": row.balance_after,
        "provider_note": row.provider_note,
        "created_at": row.created_at.isoformat(), "updated_at": row.updated_at.isoformat(),
        "acknowledged_at": row.acknowledged_at.isoformat() if row.acknowledged_at else None,
        "completed_at": row.completed_at.isoformat() if row.completed_at else None,
        "applied_at": row.applied_at.isoformat() if row.applied_at else None,
    }


@router.get("")
def list_requests(
    status: Optional[str] = None,
    principal: Principal = Depends(current_principal),
    session: Session = Depends(get_session),
):
    q = select(CashSupportRequest).order_by(CashSupportRequest.created_at.desc())
    if principal.role == "provider":
        if not principal.provider:
            return {"requests": []}
        q = q.where(CashSupportRequest.provider == principal.provider)
    elif principal.role == "agent":
        if principal.agent_id is None:
            return {"requests": []}
        q = q.where(CashSupportRequest.agent_id == principal.agent_id)
    elif principal.role not in ("ops", "risk", "management"):
        raise HTTPException(403, "role cannot view cash-support requests")
    if status:
        q = q.where(CashSupportRequest.status == status)
    rows = session.exec(q.limit(100)).all()
    return {"requests": [serialize(row, session) for row in rows]}


@router.post("/{request_id}/action")
def provider_action(
    request_id: int,
    payload: dict,
    principal: Principal = Depends(current_principal),
    session: Session = Depends(get_session),
):
    row = session.get(CashSupportRequest, request_id)
    if row is None:
        raise HTTPException(404, "cash-support request not found")
    if principal.role != "provider" or principal.provider != row.provider:
        raise HTTPException(403, "only the requested financial service provider may act")
    action = (payload.get("action") or "").strip()
    allowed = {
        "acknowledge": (("requested",), "acknowledged"),
        "approve": (("requested", "acknowledged"), "approved"),
        "reject": (("requested", "acknowledged"), "rejected"),
        "fulfil": (("approved",), "fulfilled"),
    }
    if action not in allowed:
        raise HTTPException(400, "action must be acknowledge, approve, reject, or fulfil")
    valid_from, new_status = allowed[action]
    if row.status not in valid_from:
        raise HTTPException(409, f"cannot {action} a request in {row.status} state")
    now = datetime.utcnow()
    row.status = new_status
    row.provider_note = (payload.get("note") or "").strip()
    row.updated_at = now
    if new_status in ("acknowledged", "approved") and row.acknowledged_at is None:
        row.acknowledged_at = now
    if new_status in ("rejected", "fulfilled"):
        row.completed_at = now
    if new_status == "approved" and row.amount is None:
        try:
            size_request(session, row)
        except ValueError as exc:
            raise HTTPException(409, str(exc))
    session.add(row)
    session.commit()
    session.refresh(row)
    alert = session.get(Alert, row.alert_id)
    case = session.exec(select(Case).where(Case.alert_id == row.alert_id)).first()
    if alert is not None and case is not None:
        event_text = {
            "acknowledged": "Provider acknowledged the support request.",
            "approved": f"Provider approved the {row.amount:,.0f} BDT support coordination plan.",
            "rejected": "Provider rejected the support request.",
            "fulfilled": "Provider recorded support coordination as completed outside this platform.",
        }[new_status]
        if row.provider_note:
            event_text += f" Note: {row.provider_note}"
        now_iso = now.isoformat()
        notes = json.loads(case.notes_json or "[]")
        audit = json.loads(case.audit_json or "[]")
        notes.append({"ts": now_iso, "role": "provider", "user": principal.username, "text": event_text})
        audit.append({
            "ts": now_iso, "from_state": case.state, "to_state": case.state,
            "actor": principal.username, "actor_role": "provider",
            "reason": f"cash_support_{new_status}",
            "owner_from": case.owner_role, "owner_to": case.owner_role,
        })
        case.notes_json, case.audit_json, case.updated_at = json.dumps(notes), json.dumps(audit), now
        session.add(case)
        add_notification(
            session, alert=alert, case=case, recipient_role="agent",
            recipient_agent_id=row.agent_id, event=f"cash_support_{new_status}",
            message=event_text, actor=principal.username,
        )
        session.commit()
    return serialize(row, session)
