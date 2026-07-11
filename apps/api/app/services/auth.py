"""Simple RBAC for the prototype — username/role is passed in the `X-User`
header. Real product would integrate with an IdP; for the hackathon this is
enough to actually enforce provider walls (not just theme them differently)."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from fastapi import Depends, Header, HTTPException, status
from sqlmodel import Session, select

from ..db import get_session
from ..models.database import User


@dataclass
class Principal:
    username: str
    display_name: str
    role: str
    provider: Optional[str]
    area: Optional[str]
    # Username -> Agent.id convention for role="agent". Populated below; agents
    # have a username of the form "agent_<agent_id>" today, but keeping this
    # explicit makes future IdP integrations easier.
    agent_id: Optional[int] = None


def _principal_agent_id(username: str) -> Optional[int]:
    # Convention: agent users have a username of the form "agent_<id>" OR an
    # exact match against an Agent.code. The seed user "agent" owns the demo
    # agent (id=1) so the existing UX keeps working.
    if username.startswith("agent_"):
        try:
            return int(username.split("_", 1)[1])
        except ValueError:
            return None
    if username == "agent":
        return 1
    return None


def _resolve(x_user, session):
    if not x_user:
        u = session.exec(select(User).where(User.username == "agent")).first()
        if u is None:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED,
                                detail="No X-User header and no fallback user.")
        return Principal(u.username, u.display_name, u.role, u.provider, u.area,
                         agent_id=_principal_agent_id(u.username))
    u = session.exec(select(User).where(User.username == x_user)).first()
    if u is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED,
                            detail="Unknown user: " + x_user)
    return Principal(u.username, u.display_name, u.role, u.provider, u.area,
                     agent_id=_principal_agent_id(u.username))


def current_principal(
    x_user: Optional[str] = Header(default=None, alias="X-User"),
    session: Session = Depends(get_session),
) -> Principal:
    return _resolve(x_user, session)


# Role-capability matrix. NOTE: this is the ground truth mirrored by the UI.
ROLE_PERMISSIONS = {
    "agent":      {"can_see_own_agent_only": True,  "can_act_on_alerts": True,  "can_dispatch": False, "can_see_all_providers": True},
    "ops":        {"can_see_own_agent_only": False, "can_act_on_alerts": True,  "can_dispatch": True,  "can_see_all_providers": True},
    "risk":       {"can_see_own_agent_only": False, "can_act_on_alerts": False, "can_dispatch": False, "can_see_all_providers": True},
    "provider":   {"can_see_own_agent_only": False, "can_act_on_alerts": True,  "can_dispatch": False, "can_see_all_providers": False},
    "management": {"can_see_own_agent_only": False, "can_act_on_alerts": False, "can_dispatch": False, "can_see_all_providers": True},
}


def require(p: Principal, capability: str) -> None:
    perms = ROLE_PERMISSIONS.get(p.role, {})
    if not perms.get(capability, False):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN,
                            detail=str(p.role) + " cannot " + capability)
