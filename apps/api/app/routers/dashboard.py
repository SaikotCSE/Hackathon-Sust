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
from ..models.database import Agent, Alert, AnomalyEvent, BalanceHistory
from ..services.auth import Principal, current_principal
from ..services.liquidity import rate_projection
from ..services.snapshots import agent_snapshot, batch_agent_snapshots, overall_score
from ..simulation.engine import PROVIDERS


# ---------------------------------------------------------------------------
# Management-rollup helpers
# ---------------------------------------------------------------------------

def _pressure_tier(score: int, reason: str) -> str:
    """Map a 0..100 pressure score onto the same 4-tier + 1-unknown scale used
    in Module 5. Critical reason keywords force the 'critical' tier even if the
    score is below 81 — e.g. an empty e-money pool should not silently render
    as 'low' just because the rolling score happened to land below the threshold
    this tick. 'data quality' reasons stay 'unknown' because we genuinely don't
    have the data to project."""
    r = (reason or "").lower()
    if "empty" in r:
        return "critical"
    if "data quality" in r or "no forecast" in r:
        return "unknown"
    if score >= 81:
        return "critical"
    if score >= 61:
        return "high"
    if score >= 31:
        return "low"
    return "normal"


def overall_score_for_provider(
    balances: dict,
    forecasts: dict,
    data_quality_by_provider: dict,
    providers_out: list,
) -> tuple:
    """Thin wrapper around snapshots.overall_score that takes already-built
    provider blocks (used when we re-score after applying the management
    provider filter — we may end up with only 1 or 2 providers per agent
    instead of the full three)."""
    return overall_score(balances, forecasts, data_quality_by_provider)


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
    "forecast_summary",
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
    area: Optional[str] = Query(default=None, description="[Management rollup only] filter to one area or area-prefix."),
    provider: Optional[str] = Query(default=None, description="[Management rollup only] filter to one provider column ('bkash'|'nagad'|'rocket')."),
    mgr_agent: Optional[int] = Query(default=None, alias="mgr_agent", description="[Management rollup only] restrict to one agent_id."),
    since_minutes: Optional[int] = Query(default=None, ge=1, le=60 * 24 * 30, description="[Management rollup only] recurring-problems window in minutes (default 7 days)."),
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
        agent_ids = [a.id for a in agents]
        # Batch: 4 queries total instead of N×(per-agent queries)
        snapshots = batch_agent_snapshots(session, agent_ids)
        per_agent = []
        for snap in snapshots:
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

    # ----- Management: aggregated risk-by-geography, no drill-in actions.
    # The original implementation summed scores and counted alerts but did
    # not:
    #   (a) accept any filter (provider/area/agent/time),
    #   (b) bucket agents into pressure tiers,
    #   (c) surface recurring-problem patterns over time (24h / 7d windows),
    #   (d) flag agents/areas whose underlying data is incomplete.
    # All four are core to the management role's job ("see area-level service
    # risk, recurring problems, and overall operational readiness").
    if role == "management":
        from datetime import datetime, timedelta
        from sqlalchemy import func as sa_func

        # ---------- build the filtered agent set -----------------------------
        q = select(Agent)
        if area:
            q = q.where(
                (Agent.area == area)
                | Agent.area.startswith(f"{area}-")
                | Agent.area.startswith(f"{area} ")
            )
        if mgr_agent is not None:
            q = q.where(Agent.id == mgr_agent)
        agents = session.exec(q.order_by(Agent.area, Agent.id)).all()

        # ---------- snapshots (with optional provider filter) ----------------
        per_area: dict[str, dict] = {}
        skipped_agents = 0
        network_buckets = {"normal": 0, "low": 0, "high": 0, "critical": 0, "unknown": 0}
        network_open_alerts = 0
        network_critical_alerts = 0
        network_pressure_sum = 0
        network_agents_seen = 0
        network_incomplete_providers = 0
        network_providers_total = 0
        # Per-agent rows we'll emit for both the per-area table and the
        # agent-level rollup at the top of the page.
        all_agent_rows: list[dict] = []

        # Build all per-agent snapshots in one batched call instead of
        # N × ~14 individual queries. The batched snapshots preserve
        # the exact same shape the rest of the management rollup expects.
        all_snaps = batch_agent_snapshots(session, [a.id for a in agents])
        snap_by_agent = {s["agent_id"]: s for s in all_snaps}

        for a in agents:
            snap = snap_by_agent.get(a.id)
            if not snap:
                skipped_agents += 1
                continue
            # Optionally filter the snapshot down to a single provider.
            if provider:
                kept = [p for p in snap.get("providers", []) if p.get("provider") == provider]
                if not kept:
                    # this agent has no data for the requested provider at all
                    skipped_agents += 1
                    continue
                snap["providers"] = kept
                # Re-derive overall_score from the filtered set so the rollup
                # is consistent with what the user sees.
                from ..services.snapshots import _provider_health
                p_balances = {p["provider"]: p.get("balance") or 0.0 for p in kept}
                p_forecasts = {}
                dq_map = {p["provider"]: p.get("data_quality") or 1.0 for p in kept}
                score, reason = overall_score_for_provider(p_balances, p_forecasts, dq_map, kept)
                snap["overall_score"] = score
                snap["overall_reason"] = reason

            bucket = per_area.setdefault(a.area, {
                "area": a.area,
                "agents": 0,
                "open_alerts": 0,
                "critical_alerts": 0,
                "pressure_score_sum": 0,
                "agents_detail": [],
                # counts of agents in this area falling into each pressure bucket
                "pressure_buckets": {"normal": 0, "low": 0, "high": 0, "critical": 0, "unknown": 0},
                # how many of this area's agents have at least one provider with
                # missing or stale data — surfaces the "incomplete data" flag.
                "agents_with_incomplete_data": 0,
                "providers_seen": 0,
                "providers_with_signal": 0,
            })
            bucket["agents"] += 1
            score = int(snap.get("overall_score", 0))
            bucket["pressure_score_sum"] += score

            # pressure-tier buckets
            tier = _pressure_tier(score, snap.get("overall_reason", ""))
            bucket["pressure_buckets"][tier] += 1
            network_buckets[tier] += 1
            network_pressure_sum += score
            network_agents_seen += 1

            # alert counts for this agent
            open_alerts = 0
            critical_alerts = 0
            for al in snap.get("alerts", []):
                if al.get("status") in ("resolved", "closed"):
                    continue
                # provider filter: skip alerts that don't belong to the requested provider
                if provider and al.get("provider") != provider:
                    continue
                open_alerts += 1
                network_open_alerts += 1
                if al.get("severity") == "critical":
                    critical_alerts += 1
                    network_critical_alerts += 1
            bucket["open_alerts"] += open_alerts
            bucket["critical_alerts"] += critical_alerts

            # data-completeness — count providers whose forecast is missing
            # or whose data_quality is below the safe threshold.
            n_providers = len(snap.get("providers") or [])
            n_with_signal = 0
            n_incomplete = 0
            for p in snap.get("providers") or []:
                network_providers_total += 1
                dq = float(p.get("data_quality") or 0.0)
                if dq < 0.4 or p.get("hours_to_shortage") is None:
                    n_incomplete += 1
                    network_incomplete_providers += 1
                else:
                    n_with_signal += 1
            if n_incomplete > 0:
                bucket["agents_with_incomplete_data"] += 1
            bucket["providers_seen"] += n_providers
            bucket["providers_with_signal"] += n_with_signal

            agent_row = {
                "agent_id": snap["agent_id"],
                "agent_code": snap["agent_code"],
                "display_name": snap["display_name"],
                "area": a.area,
                "overall_score": score,
                "open_alerts": open_alerts,
                "critical_alerts": critical_alerts,
                "pressure_tier": tier,
                "providers_seen": n_providers,
                "providers_with_signal": n_with_signal,
                "incomplete_data": n_incomplete > 0,
                "overall_reason": snap.get("overall_reason", ""),
                "provider_filter": provider or None,
            }
            bucket["agents_detail"].append(agent_row)
            all_agent_rows.append(agent_row)

        for b in per_area.values():
            if b["agents"]:
                b["avg_pressure"] = round(b["pressure_score_sum"] / b["agents"], 1)
            else:
                b["avg_pressure"] = 0
            del b["pressure_score_sum"]
            # data-completeness ratio: 1.0 = all providers healthy.
            if b["providers_seen"]:
                b["data_completeness"] = round(b["providers_with_signal"] / b["providers_seen"], 2)
            else:
                b["data_completeness"] = 1.0

        # ---------- recurring-problems rollup (24h + 7d windows) -------------
        # A "recurring problem" = an agent or area that appears multiple times
        # within the lookback window. We count open alerts and anomaly events
        # by (agent, area, provider) for two windows so a manager can see both
        # today's hot spots and the slow-burn repeat offenders.
        window_minutes = since_minutes if since_minutes is not None else 60 * 24 * 7
        cutoff_24h = datetime.utcnow() - timedelta(hours=24)
        cutoff_window = datetime.utcnow() - timedelta(minutes=window_minutes)

        # Open alerts (any non-terminal status) per agent within each window.
        from sqlalchemy import case as sa_case
        recurring_open_q = (
            select(
                Alert.agent_id,
                Agent.area,
                Alert.provider,
                sa_func.sum(
                    sa_case((Alert.created_at >= cutoff_24h, 1), else_=0)
                ).label("open_24h"),
                sa_func.count(Alert.id).label("open_window"),
            )
            .join(Agent, Agent.id == Alert.agent_id)
            .where(Alert.status.notin_(("resolved", "closed")))
        )
        if provider:
            recurring_open_q = recurring_open_q.where(Alert.provider == provider)
        if mgr_agent is not None:
            recurring_open_q = recurring_open_q.where(Alert.agent_id == mgr_agent)
        if area:
            recurring_open_q = recurring_open_q.where(
                (Agent.area == area)
                | Agent.area.startswith(f"{area}-")
                | Agent.area.startswith(f"{area} ")
            )
        recurring_open_rows = session.exec(
            recurring_open_q.where(Alert.created_at >= cutoff_window)
            .group_by(Alert.agent_id, Agent.area, Alert.provider)
            .order_by(sa_func.count(Alert.id).desc())
        ).all()

        recurring = []
        for agent_id_val, agent_area, agent_provider, open_24h, open_window in recurring_open_rows:
            agent_obj = session.get(Agent, agent_id_val)
            recurring.append({
                "agent_id": agent_id_val,
                "agent_code": agent_obj.code if agent_obj else f"#{agent_id_val}",
                "display_name": agent_obj.display_name if agent_obj else f"Agent #{agent_id_val}",
                "area": agent_area,
                "provider": agent_provider,
                "open_24h": int(open_24h or 0),
                "open_window": int(open_window or 0),
                "is_repeat_offender": (open_window or 0) >= 2,
            })

        # Anomaly events per area for the same window — surfaces "this area
        # keeps firing anomaly rules" even when no Alert has been promoted yet.
        anomaly_q = (
            select(
                Agent.area,
                AnomalyEvent.provider,
                sa_func.count(AnomalyEvent.id).label("anomaly_count"),
            )
            .join(Agent, Agent.id == AnomalyEvent.agent_id)
            .where(AnomalyEvent.ts >= cutoff_window)
        )
        if provider:
            anomaly_q = anomaly_q.where(AnomalyEvent.provider == provider)
        if mgr_agent is not None:
            anomaly_q = anomaly_q.where(AnomalyEvent.agent_id == mgr_agent)
        if area:
            anomaly_q = anomaly_q.where(
                (Agent.area == area)
                | Agent.area.startswith(f"{area}-")
                | Agent.area.startswith(f"{area} ")
            )
        anomaly_rows = session.exec(
            anomaly_q.group_by(Agent.area, AnomalyEvent.provider).order_by(sa_func.count(AnomalyEvent.id).desc())
        ).all()
        anomaly_rollup = [
            {"area": a, "provider": p, "anomaly_count": int(c or 0)}
            for a, p, c in anomaly_rows
        ]

        # ---------- network-wide completeness + scope echo -------------------
        avg_pressure = (
            round(network_pressure_sum / network_agents_seen, 1)
            if network_agents_seen else 0
        )
        network_data_completeness = (
            round(
                1.0 - (network_incomplete_providers / network_providers_total),
                2,
            )
            if network_providers_total else 1.0
        )

        return {
            "view": "management",
            "scope": {
                "area_count": len(per_area),
                "agent_count": network_agents_seen,
                "skipped_agents": skipped_agents,
                "filters": {
                    "area": area,
                    "provider": provider,
                    "agent_id": mgr_agent,
                    "since_minutes": window_minutes,
                },
                "data_completeness": network_data_completeness,
                "incomplete_provider_count": network_incomplete_providers,
                "provider_count": network_providers_total,
            },
            "areas": list(per_area.values()),
            "network": {
                "avg_pressure": avg_pressure,
                "open_alerts": network_open_alerts,
                "critical_alerts": network_critical_alerts,
                "pressure_buckets": network_buckets,
                "agents_total": len(agents),
                "agents_seen": network_agents_seen,
            },
            "recurring_problems": {
                "window_minutes": window_minutes,
                "by_agent": recurring,
                "anomaly_rollup": anomaly_rollup,
            },
            "agents": all_agent_rows,
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
        # Batch: one pass over the agents instead of N × 14 queries each.
        per_agent = batch_agent_snapshots(session, [a.id for a in agents])
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
            "summary": rp.summary,
        })
    return {"agent_id": agent_id, "series": out}