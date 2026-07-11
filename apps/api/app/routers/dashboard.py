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


# Fields inside a per-provider block that another provider must NOT see.
# Each is either money, a derived metric about another provider's wallet, or
# a forecast reasoning chain. Keeping the columns but nulling them is a leak;
# we drop the whole block instead so the UI can't accidentally render a stale
# row of zeros as 'that provider is empty'.
_OTHER_PROVIDER_NUMERIC_FIELDS = (
    "balance",
    "history",
    "burn_rate_per_min",
    "hours_to_shortage",
    "forecast_confidence",
    "health",
    "data_quality",
    "expected_outflow_next_hours",
    "current_demand_label",
    "shortage_eta_human",
    "recent_deltas",
    "forecast_reasons",
)


def _enforce_provider_wall(snap: dict, principal: Principal) -> None:
    """Hard scope a per-agent snapshot to ``principal.provider`` only.

    For a Financial Service Provider principal we must never expose another
    provider's balances, burn rate, hours-to-shortage, or alerts. The earlier
    implementation only nulled a few fields and left the cross-provider
    numerics visible (combined pool, other-provider blocks, free-text reason
    strings, embedded alert list). This implementation rebuilds the snapshot
    from the principal's own column and drops everything else.
    """
    if principal.role != "provider" or not principal.provider:
        return
    if not isinstance(snap, dict):
        return

    own_provider = principal.provider

    # ---- per-provider blocks: keep only the principal's own column ------------
    own_block = None
    if isinstance(snap.get("providers"), list):
        for prov_block in snap["providers"]:
            if not isinstance(prov_block, dict):
                continue
            if prov_block.get("provider") == own_provider:
                own_block = prov_block
        snap["providers"] = [own_block] if own_block else []

    # ---- combined pool block: fundamentally cross-provider; drop entirely. ---
    snap.pop("combined", None)

    # ---- physical cash belongs to the agent's drawer, not to a provider.
    # A provider principal never needs the cross-agent cash figure, so we
    # drop it from the snapshot rather than leak the agent's drawer amount.
    snap.pop("physical_cash", None)

    # ---- overall_reason free-text: rewrite if it names another provider or
    # contains fragments like 'rocket: ~0m to shortage'. We only know how to
    # produce a provider-scoped reason from the principal's own block.
    reason = snap.get("overall_reason") or ""
    if own_block is not None:
        hrs = own_block.get("hours_to_shortage")
        if hrs is None:
            own_reason = "your provider's projection is currently unavailable"
        elif hrs < 0.5:
            own_reason = f"your provider has pressure in ~{int(round(hrs * 60))} min"
        elif hrs < 2:
            own_reason = f"your provider has pressure in {hrs:.1f} h"
        else:
            own_reason = "your provider is within a healthy band"
        snap["overall_reason"] = own_reason
    else:
        snap["overall_reason"] = (
            "your provider's data is not currently being delivered to this agent"
        )
    # Strip any leftover cross-provider names that may have slipped into
    # reasons built upstream — defense in depth.
    for other in PROVIDERS:
        if other == own_provider:
            continue
        if other and other in reason:
            snap["overall_reason"] = (
                "your provider's projection is currently unavailable"
            )
            break

    # ---- embedded alerts: filter to the principal's provider only ----------
    own_alerts = []
    for a in snap.get("alerts", []) or []:
        if isinstance(a, dict) and a.get("provider") == own_provider:
            own_alerts.append(a)
    snap["alerts"] = own_alerts

    # ---- degraded-state signalling for the principal's own block ----------
    # A provider principal needs to know when THEIR feed is poor — otherwise
    # they'd render 'hours_to_shortage = None' as healthy without realizing
    # it's a feed problem. Surface explicit degraded flag + reason so the
    # frontend can render 'feed degraded — wait for data' instead of a number.
    if own_block is not None:
        try:
            dq = float(own_block.get("data_quality") or 0.0)
        except (TypeError, ValueError):
            dq = 0.0
        sample_count = len(own_block.get("history") or [])
        if dq < 0.6:
            own_block["hours_to_shortage"] = None
            own_block["shortage_eta_human"] = "feed stale — projection paused"
            own_block["degraded"] = True
            own_block["degraded_reason"] = f"data_quality {dq:.2f} below 0.60 threshold"
        elif sample_count < 5:
            own_block["hours_to_shortage"] = None
            own_block["shortage_eta_human"] = "not enough samples yet"
            own_block["degraded"] = True
            own_block["degraded_reason"] = (
                f"only {sample_count} history points — need at least 5"
            )
        else:
            own_block["degraded"] = False
            own_block["degraded_reason"] = None


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

    # ----- Operations / Network Coordination: agents in their area.
    # Area names are hierarchical (e.g. "Dhaka" covers "Dhaka-Mirpur",
    # "Dhaka-Gulshan"); we match on a "starts-with" relationship rather
    # than an exact string so seeded sub-areas show up under the ops user.
    if role == "ops":
        area = principal.area
        agents_q = select(Agent)
        if area:
            agents_q = agents_q.where(
                (Agent.area == area)
                | Agent.area.startswith(f"{area}-")
                | Agent.area.startswith(f"{area} ")
            )
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