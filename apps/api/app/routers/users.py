"""User listing endpoint — used by the role-switcher in the UI."""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlmodel import Session, select

from ..db import get_session
from ..models.database import User
from ..services.auth import Principal, current_principal


router = APIRouter(tags=["users"])


@router.get("/users")
def list_users(
    principal: Principal = Depends(current_principal),
    session: Session = Depends(get_session),
):
    rows = session.exec(select(User).order_by(User.role, User.username)).all()
    return {"users": [
        {
            "username": u.username,
            "display_name": u.display_name,
            "role": u.role,
            "provider": u.provider,
            "area": u.area,
        }
        for u in rows
    ]}