"""Role-scoped notification inbox for case handoffs and stakeholder reactions."""
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session, select

from ..db import get_session
from ..models.database import StakeholderNotification
from ..services.auth import Principal, current_principal

router = APIRouter(prefix="/notifications", tags=["notifications"])


def _visible(row: StakeholderNotification, principal: Principal) -> bool:
    if row.recipient_role != principal.role:
        return False
    if principal.role == "agent":
        return row.recipient_agent_id == principal.agent_id
    if principal.role == "provider":
        return row.recipient_provider == principal.provider
    if principal.role == "ops" and row.recipient_area and principal.area:
        return row.recipient_area == principal.area or row.recipient_area.startswith(principal.area)
    return True


def serialize(row: StakeholderNotification) -> dict:
    return {
        "id": row.id, "alert_id": row.alert_id, "case_id": row.case_id,
        "event": row.event, "title": row.title, "message": row.message,
        "actor": row.actor, "created_at": row.created_at.isoformat(),
        "read_at": row.read_at.isoformat() if row.read_at else None,
    }


@router.get("")
def list_notifications(
    principal: Principal = Depends(current_principal),
    session: Session = Depends(get_session),
):
    rows = session.exec(
        select(StakeholderNotification)
        .where(StakeholderNotification.recipient_role == principal.role)
        .order_by(StakeholderNotification.created_at.desc()).limit(100)
    ).all()
    visible = [row for row in rows if _visible(row, principal)]
    return {"notifications": [serialize(row) for row in visible],
            "unread": sum(row.read_at is None for row in visible)}


@router.post("/{notification_id}/read")
def mark_read(
    notification_id: int,
    principal: Principal = Depends(current_principal),
    session: Session = Depends(get_session),
):
    row = session.get(StakeholderNotification, notification_id)
    if row is None:
        raise HTTPException(404, "notification not found")
    if not _visible(row, principal):
        raise HTTPException(403, "notification belongs to another stakeholder")
    if row.read_at is None:
        row.read_at = datetime.utcnow()
        session.add(row); session.commit(); session.refresh(row)
    return serialize(row)
