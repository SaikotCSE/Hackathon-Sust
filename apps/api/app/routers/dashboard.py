"""Dashboard / snapshot endpoints — read-side of Module 1.

The endpoint is role-shaped: an agent sees only their own shop; ops sees every
agent in their area; risk sees the network-wide queue; a provider sees only
their provider's column across the agents they cover; management sees an
aggregated risk-by-geography view. This is the actual RBAC layer the brief
calls for — not just CSS theming.
"""
from __future__ import annotations

import json
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlmodel import Session, select

from ..db import get_session
from ..models.database import Agent, Alert, BalanceHistory
from ..services.auth import Principal, current_principal
from ..services.liquidity import rate_projection
from ..services.snapshots import agent_snapshot
from ..simulation.engine import PROVIDERS


router = APIRouter(tags=["dashboard"])


def _enforce_provider_wall(snap: dict, principal: Principal) -> None:
    if not snap.get("providers"):
        return
    for prov_block in snap["providers"]:
        if principal.role == "provider" and principal.provider and prov_block["provider"] != principal.provider:
            prov_block["balance"] = None
            prov_block["history"] = []
            prov_block["forecast_reasons"] = []


@router.get("/dashboard")
def dashboard(
    agent_id: int = 1,
    principal: Principal = Depends(current_principal),
    session: Session = Depends(get_session),
):
    """Role-shaped dashboard.

    Same shape for agents (single-shop drill-in); different shapes for the
    other roles. The frontend reads the role from the `principal` block and
    picks the matching component.
    """
    principal_block = {
        "username": principal.username,
        "display_name": principal.display_name,
        "role": principal.role,
        "provider": principal.provider,
        "area": principal.area,
        "agent_id": principal.agent_id,
    }

    role = principal.role

    # ----- Provider: only their provider's column across the agents they cover
    if role == "provider" and principal.provider:
        agents = session.exec(select(Agent).order_by(Agent.id)).all()
        per_agent = []
        for a in agents:
            snap = agent_snapshot(session, a.id)
            if not snap:
                continue
            _enforce_provider_wall(snap, principal)
            per_agent.append(snap)
        return {
            "view": "provider",
            "scope": {
                "provider": principal.provider,
                "agent_count": len(per_agent),
            },
            "per_agent": per_agent,
            "principal": principal_block,
        }

    # ----- Management: aggregated risk-by-geography, no drill-in actions
    if role == "management":
        agents = session.exec(select(Agent).order_by(Agent.area, Agent.id)).all()
        per_area: dict[str, dict] = {}
        for a in agents:
            snap = agent_snapshot(session, a.id)
            if not snap:
                continue
            bucket = per_area.setdefault(a.area, {
                "area": a.area,
                "agents": 0,
                "open_alerts": 0,
                "critical_alerts": 0,
                "pressure_score_sum": 0,
                "agents_detail": [],
            })
            bucket["agents"] += 1
            bucket["pressure_score_sum"] += snap.get("overall_score", 0)
            for al in snap.get("alerts", []):
                if al.get("status") in ("resolved", "closed"):
                    continue
                bucket["open_alerts"] += 1
                if al.get("severity") == "critical":
                    bucket["critical_alerts"] += 1
            bucket["agents_detail"].append({
                "agent_id": snap["agent_id"],
                "agent_code": snap["agent_code"],
                "display_name": snap["display_name"],
                "overall_score": snap["overall_score"],
                "open_alerts": len([a for a in snap.get("alerts", [])
                                    if a.get("status") not in ("resolved", "closed")]),
            })
        for b in per_area.values():
            if b["agents"]:
                b["avg_pressure"] = round(b["pressure_score_sum"] / b["agents"], 1)
            else:
                b["avg_pressure"] = 0
        return {
            "view": "management",
            "scope": {"area_count": len(per_area)},
            "areas": list(per_area.values()),
            "principal": principal_block,
        }

    # ----- Operations / Network Coordination: agents in their area
    if role == "ops":
        area = principal.area
        agents_q = select(Agent)
        if area:
            agents_q = agents_q.where(Agent.area == area)
        agents = session.exec(agents_q.order_by(Agent.id)).all()
        per_agent = [agent_snapshot(session, a.id) for a in agents]
        per_agent = [s for s in per_agent if s]
        return {
            "view": "ops",
            "scope": {"area": area, "agent_count": len(per_agent)},
            "per_agent": per_agent,
            "principal": principal_block,
        }

    # ----- Risk/Compliance: network-wide escalated cases (and pending)
    if role == "risk":
        alerts = session.exec(
            select(Alert)
            .where(Alert.status.in_(("escalated", "compliance_decision", "under_review")))
            .order_by(Alert.priority_score.desc(), Alert.created_at.desc())
            .limit(50)
        ).all()
        items = []
        for a in alerts:
            items.append({
                "id": a.id, "agent_id": a.agent_id, "provider": a.provider,
                "severity": a.severity, "priority_score": a.priority_score,
                "title": a.title, "summary": a.summary,
                "status": a.status, "owner_role": a.owner_role,
                "owner_label": a.owner_label, "confidence": a.confidence,
                "reasons": json.loads(a.reasons_json or "[]"),
                "evidence": json.loads(a.evidence_json or "[]"),
                "created_at": a.created_at.isoformat(),
            })
        return {
            "view": "risk",
            "scope": {"queue_size": len(items)},
            "queue": items,
            "principal": principal_block,
        }

    # ----- Agent: their own shop, enforced
    if role == "agent":
        own_id = principal.agent_id or 1
        if principal.agent_id and agent_id != own_id:
            raise HTTPException(status_code=403,
                                detail="Agent principals see only their own shop.")
        snap = agent_snapshot(session, own_id)
        if not snap:
            return {"error": "agent not found"}
        return {
            "view": "agent",
            "scope": {"agent_id": own_id},
            **snap,
            "principal": principal_block,
        }

    # Unknown role — fall back to a single agent view, but don't leak data
    snap = agent_snapshot(session, 1) or {}
    return {"view": "agent", "scope": {"agent_id": 1}, **snap, "principal": principal_block}


@router.get("/dashboard/series")
def dashboard_series(
    agent_id: int = 1,
    provider: Optional[str] = None,
    limit: int = 60,
    principal: Principal = Depends(current_principal),
    session: Session = Depends(get_session),
):
    """Chart-ready history for the agent dashboard.

    Returns ordered `(ts, balance)` points per provider (oldest → newest) plus
    the current burn rate (BDT/min) so the UI can draw a depletion projection.
    The provider wall is enforced here too — a 'provider' role only sees their
    own column. Refreshes whenever SWR re-fires on the dashboard.
    """
    # RBAC: agents only see their own agent_id
    if principal.role == "agent" and principal.agent_id and agent_id != principal.agent_id:
        raise HTTPException(status_code=403, detail="Agent principals see only their own shop.")

    # Provider-wall: a 'provider' principal can only query their own provider's column.
    if principal.role == "provider" and principal.provider:
        provider = principal.provider

    chosen = [provider] if provider else list(PROVIDERS)
    out = []
    for prov in chosen:
        rows = session.exec(
            select(BalanceHistory)
            .where(BalanceHistory.agent_id == agent_id)
            .where(BalanceHistory.provider == prov)
            .order_by(BalanceHistory.ts.asc())
            .limit(limit)
        ).all()
        rp = rate_projection(session, agent_id, prov)
        out.append({
            "provider": prov,
            "points": [{"ts": r.ts.isoformat(), "balance": r.balance} for r in rows],
            "burn_rate_per_min": rp.burn_rate_per_min,
            "hours_to_shortage": rp.hours_to_shortage,
            "confidence": rp.confidence,
        })
    return {"agent_id": agent_id, "series": out}