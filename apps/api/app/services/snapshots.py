"""Dashboard snapshot — assembles Module 1's per-agent view in one call."""
from __future__ import annotations

import json
from collections import deque
from datetime import datetime, timedelta
from typing import Dict, List, Optional

from sqlmodel import Session, select

from ..models.database import (
    Alert,
    Agent,
    BalanceHistory,
    ForecastSnapshot,
    ProviderBalance,
    Transaction,
)
from ..simulation.engine import PROVIDERS


def _demand_label(expected_outflow: Optional[float], burn_rate_per_min: float) -> str:
    """Map an outflow forecast onto a coarse low/medium/high bucket the UI can
    render as a single line on the provider card."""
    if expected_outflow is None or burn_rate_per_min <= 0:
        return "low"
    # 8-hour window worth of expected outflow at the current burn rate.
    window = burn_rate_per_min * 60 * 8
    ratio = expected_outflow / max(window, 1.0)
    if ratio >= 1.1:
        return "high"
    if ratio >= 0.6:
        return "medium"
    return "low"


def _shortage_eta_human(hours: Optional[float]) -> str:
    if hours is None:
        return "no projection"
    if hours < 0:
        return "— hours"
    if hours < 1:
        return f"~{int(round(hours * 60))} min"
    return f"~{hours:.1f} hours"


def _recent_deltas(history: List[float], n: int = 3) -> List[float]:
    """Last n step-on-step deltas of the history list (most recent last)."""
    if not history:
        return []
    out = []
    for i in range(max(0, len(history) - n), len(history)):
        prev = history[i - 1] if i > 0 else history[i]
        out.append(round(history[i] - prev, 2))
    return out


def _expected_outflow(burn_rate_per_min: float, hours: float = 4.0) -> float:
    """Simple forward-looking outflow projection used for the 'Provider-Aware
    Demand' block. Falls back to zero when no burn rate is known."""
    return round(max(burn_rate_per_min, 0.0) * 60.0 * hours, 2)


def _provider_health(balance: float, hours_to_shortage: Optional[float], data_quality: float) -> str:
    if data_quality < 0.4:
        return "unknown"
    if balance <= 0:
        return "critical"
    if hours_to_shortage is None or hours_to_shortage > 6:
        return "normal"
    if hours_to_shortage < 0.5:
        return "critical"
    if hours_to_shortage < 2:
        return "high"
    return "low"


def overall_score(
    balances: Dict[str, float],
    forecasts: Dict[str, Optional[ForecastSnapshot]],
    data_quality_by_provider: Optional[Dict[str, float]] = None,
) -> tuple:
    """0..100. Lower = healthier.

    Each provider contributes its projected health:
      - balance == 0 → 100 (critical, e-money pool empty)
      - hours_to_shortage < 0.5h → 95
      - hours_to_shortage < 2h   → 70
      - hours_to_shortage < 6h   → 45
      - hours_to_shortage >= 6h  → 20
      - no forecast / hours_to_shortage is None → 60 (UNKNOWN, not healthy).
        We deliberately do NOT default to 90 here: a None forecast means we
        lack the data to project, not that the provider is healthy. This
        bug previously caused empty bkash wallets to render as healthy.
      - data_quality < 0.4 → unknown bucket, contribution 60; we also flag
        the resulting "no signal" set so the rollup can show data-completeness.
    """
    if not balances:
        return 80, "no provider data yet"
    score = 0
    weight = 0
    reasons = []
    no_signal: List[str] = []
    for prov, bal in balances.items():
        fc = forecasts.get(prov)
        dq = (data_quality_by_provider or {}).get(prov, 1.0)
        # Stale or missing data quality: treat as unknown, not healthy.
        if dq is not None and dq < 0.4:
            score += 60
            weight += 1
            no_signal.append(prov)
            reasons.append(f"{prov}: data quality {dq:.2f} — projection paused")
            continue
        # Empty wallet → critical regardless of forecast.
        if bal is not None and bal <= 0:
            score += 100
            weight += 1
            reasons.append(f"{prov}: e-money pool empty")
            continue
        if fc is None or fc.hours_to_shortage is None:
            # NOT healthy — we just don't know. Surface as unknown so the
            # rollup can treat incomplete data differently from healthy.
            score += 60
            weight += 1
            no_signal.append(prov)
            reasons.append(f"{prov}: no forecast yet")
            continue
        hrs = fc.hours_to_shortage
        if hrs < 0.5:
            contrib = 95
            reasons.append(f"{prov}: ~{int(hrs*60)}m to shortage")
        elif hrs < 2:
            contrib = 70
            reasons.append(f"{prov}: pressure in {hrs:.1f}h")
        elif hrs < 6:
            contrib = 45
            reasons.append(f"{prov}: low liquidity")
        else:
            contrib = 20
        score += contrib
        weight += 1
    if weight == 0:
        return 50, "no signals"
    avg = int(round(score / weight))
    return avg, "; ".join(reasons) or "all providers within healthy range"


def agent_snapshot(session: Session, agent_id: int) -> dict:
    """Per-agent read API (kept for single-agent callers like /alerts/{id} and the
    single-shop agent view).

    For dashboard rollups that build N agents at once, prefer
    `batch_agent_snapshots` which shares queries across agents instead of
    paying N× the per-agent DB cost.
    """
    agent = session.get(Agent, agent_id)
    if agent is None:
        return {}

    balances = {pb.provider: pb.balance for pb in session.exec(
        select(ProviderBalance).where(ProviderBalance.agent_id == agent_id)
    ).all()}

    balances = {pb.provider: pb.balance for pb in session.exec(
        select(ProviderBalance).where(ProviderBalance.agent_id == agent_id)
    ).all()}

    history_by_provider: Dict[str, List[float]] = {p: [] for p in PROVIDERS}
    for prov in PROVIDERS:
        rows = session.exec(
            select(BalanceHistory)
            .where(BalanceHistory.agent_id == agent_id)
            .where(BalanceHistory.provider == prov)
            .order_by(BalanceHistory.ts.desc())
            .limit(30)
        ).all()
        history_by_provider[prov] = [r.balance for r in reversed(rows)]

    forecasts: Dict[str, Optional[ForecastSnapshot]] = {}
    providers_out = []
    # Iterate providers + physical — physical cash goes through the same
    # forecast pipeline so the management rollup can show one consistent
    # overall_score per agent instead of "physical: no forecast yet" tags.
    for prov in list(PROVIDERS) + ["physical"]:
        fc = session.exec(
            select(ForecastSnapshot)
            .where(ForecastSnapshot.agent_id == agent_id)
            .where(ForecastSnapshot.provider == prov)
            .order_by(ForecastSnapshot.ts.desc())
            .limit(1)
        ).first()
        forecasts[prov] = fc
        try:
            reasons = json.loads(fc.reasons_json or "[]") if fc else []
        except Exception:
            reasons = []
        burn = (fc.feature_importance_json or "{}") if fc else "{}"
        # burn rate per minute stored only as feature — derive from history instead:
        from ..services.liquidity import rate_projection
        rp = rate_projection(session, agent_id, prov)
        history = history_by_provider.get(prov, [])[-20:]
        hours_to_shortage = fc.hours_to_shortage if fc else None
        expected_outflow = _expected_outflow(rp.burn_rate_per_min)
        providers_out.append({
            "provider": prov,
            "balance": balances.get(prov, 0.0),
            "health": _provider_health(balances.get(prov, 0.0), hours_to_shortage, fc.data_quality if fc else 1.0),
            "burn_rate_per_min": rp.burn_rate_per_min,
            "hours_to_shortage": hours_to_shortage,
            "forecast_confidence": fc.confidence if fc else 0.0,
            "forecast_summary": (fc.summary if fc else "") or (reasons[0] if reasons else ""),
            "forecast_reasons": reasons,
            "data_quality": fc.data_quality if fc else 1.0,
            "history": history,
            # ----- fields used by the role-aware dashboard card UI -----
            "recent_deltas": _recent_deltas(history, 3),
            "expected_outflow_next_hours": expected_outflow,
            "current_demand_label": _demand_label(expected_outflow, rp.burn_rate_per_min),
            "shortage_eta_human": _shortage_eta_human(hours_to_shortage),
        })

    open_alerts = session.exec(
        select(Alert).where(Alert.agent_id == agent_id).order_by(Alert.created_at.desc()).limit(10)
    ).all()

    dq_by_provider: Dict[str, float] = {
        p.get("provider"): p.get("data_quality", 1.0)
        for p in providers_out
        if isinstance(p, dict) and p.get("provider")
    }
    overall, overall_reason = overall_score(balances, forecasts, dq_by_provider)

    # ---- Combined / aggregate picture ------------------------------------
    # Single shop's full liquidity = physical cash on the counter + every
    # provider's e-money balance. We compute one shared burn rate (weighted
    # by data quality so a stale provider can't dominate) and project how
    # long this combined pool can keep serving customers. If confidence is
    # too low we surface a fallback instead of pretending we have a number.
    physical = float(balances.get("physical", 0.0))
    total_emoney = 0.0
    weighted_burn_num = 0.0
    weighted_burn_den = 0.0
    weighted_conf_num = 0.0
    weighted_conf_den = 0.0
    worst_dq = 1.0
    n_with_burn = 0
    n_with_h2s = 0
    for prov_block in providers_out:
        dq = float(prov_block.get("data_quality") or 0.0)
        burn = float(prov_block.get("burn_rate_per_min") or 0.0)
        conf = float(prov_block.get("forecast_confidence") or 0.0)
        bal = float(prov_block.get("balance") or 0.0)
        total_emoney += bal
        # weight by data quality so stale columns don't dominate the shared burn rate
        if burn > 0:
            weighted_burn_num += burn * max(dq, 0.05)
            weighted_burn_den += max(dq, 0.05)
            n_with_burn += 1
        if prov_block.get("hours_to_shortage") is not None:
            n_with_h2s += 1
        # confidence — use data_quality as well since it's the 'is the feed
        # healthy' signal; multiplier keeps both axes in [0,1]
        weighted_conf_num += max(min(conf, 1.0), 0.0) * max(min(dq, 1.0), 0.0)
        weighted_conf_den += 1.0
        worst_dq = min(worst_dq, dq)

    combined_burn_per_min = (
        weighted_burn_num / weighted_burn_den if weighted_burn_den > 0 else 0.0
    )
    combined_confidence = (
        weighted_conf_num / weighted_conf_den if weighted_conf_den > 0 else 0.0
    )
    total_cash = physical + total_emoney

    # shared-cash hours_to_shortage: how long until the SHARED pool (cash on
    # counter + every e-money balance) is empty at the current combined burn.
    # Confidence floor: if we have no burn signal yet OR confidence is too
    # low, we return None so the UI shows a fallback message instead of a
    # misleading number.
    combined_hours_to_shortage: Optional[float]
    notes: List[str] = []
    if total_cash <= 0:
        combined_hours_to_shortage = 0.0
        notes.append("Combined pool is empty — refill or wait for incoming transactions.")
    elif combined_burn_per_min <= 0 or n_with_burn == 0:
        combined_hours_to_shortage = None
        notes.append("Not enough burn-rate samples yet — need a few minutes of activity to project the shared pool.")
    elif combined_confidence < 0.25:
        combined_hours_to_shortage = None
        notes.append("Combined projection paused — confidence is low because some provider feeds are late or stale.")
    elif worst_dq < 0.4:
        combined_hours_to_shortage = None
        notes.append("Combined projection paused — at least one provider's data quality is below the safe threshold.")
    else:
        combined_hours_to_shortage = round(total_cash / (combined_burn_per_min * 60.0), 2)

    # narrative label the dashboard renders as the headline ("can I keep
    # serving customers for the next few hours?")
    if combined_hours_to_shortage is None:
        healthy_label = "projection unavailable right now"
        can_serve_hours_text = "—"
    elif combined_hours_to_shortage < 0.5:
        healthy_label = "combined pool will run out in under 30 minutes"
        can_serve_hours_text = f"{int(round(combined_hours_to_shortage * 60))} min"
    elif combined_hours_to_shortage < 2:
        healthy_label = "combined pool will run out in under 2 hours"
        can_serve_hours_text = f"{combined_hours_to_shortage:.1f} hours"
    elif combined_hours_to_shortage < 6:
        healthy_label = "comfortable for the next few hours"
        can_serve_hours_text = f"{combined_hours_to_shortage:.1f} hours"
    else:
        healthy_label = "comfortable — well over half a day of activity"
        can_serve_hours_text = f"{combined_hours_to_shortage:.1f} hours"

    if combined_confidence > 0:
        if combined_confidence >= 0.6:
            notes.append(f"Confidence is high ({int(round(combined_confidence * 100))}%) — projection is reliable.")
        elif combined_confidence >= 0.3:
            notes.append(f"Confidence is moderate ({int(round(combined_confidence * 100))}%) — treat the projection as advisory.")
        else:
            notes.append(f"Confidence is low ({int(round(combined_confidence * 100))}%) — wait for more data before acting on the number.")

    combined_block = {
        "total_cash": round(total_cash, 2),
        "physical_cash": round(physical, 2),
        "total_emoney": round(total_emoney, 2),
        "combined_burn_per_min": round(combined_burn_per_min, 4),
        "hours_to_shortage": combined_hours_to_shortage,
        "shortage_eta_human": _shortage_eta_human(combined_hours_to_shortage),
        "confidence": round(combined_confidence, 3),
        "data_quality": round(worst_dq, 3),  # worst-of so a bad feed trips the fallback
        "healthy_label": healthy_label,
        "can_serve_hours_text": can_serve_hours_text,
        "providers_with_burn_signal": n_with_burn,
        "providers_with_shortage_projection": n_with_h2s,
        "fallback_active": combined_hours_to_shortage is None,
        "notes": notes,
    }

    return {
        "agent_id": agent.id,
        "agent_code": agent.code,
        "display_name": agent.display_name,
        "area": agent.area,
        "physical_cash": physical,
        "overall_score": overall,
        "overall_reason": overall_reason,
        "providers": providers_out,
        "combined": combined_block,
        "alerts": [
            {
                "id": a.id,
                "provider": a.provider,
                "severity": a.severity,
                "priority_score": a.priority_score,
                "title": a.title,
                "summary": a.summary,
                "confidence": a.confidence,
                "status": a.status,
                "owner_role": a.owner_role,
                "owner_label": a.owner_label,
                "created_at": a.created_at.isoformat(),
            }
            for a in open_alerts
        ],
    }


# ---------------------------------------------------------------------------
# Batch loader for the dashboard rollup
# ---------------------------------------------------------------------------

# Per-agent cost: 1 (balances) + 3 (forecast × 4 providers incl. physical) +
# 1 (alerts) + N_providers (history) DB queries per agent. With 10 agents
# that was 100+ queries per page load.
# Batched: 4 queries total regardless of agent count.

# Cap per-provider history rows. The dashboard only renders the last 30
# points on the sparkline + uses the most recent ~60 for rate_projection
# inside agent_snapshot. We pull a bit more here (60) so the per-agent
# rate_projection (which queries up to 60 min of history) doesn't have to
# re-fetch when this batch result is reused.
_HISTORY_ROWS_PER_PROVIDER = 60

# Per-agent alert cap. The per-agent view surfaces the 10 most recent
# alerts; the batched loader follows the same ceiling.
_ALERTS_PER_AGENT = 10


def _forecast_dict(fc) -> dict:
    """Normalise a ForecastSnapshot ORM row into the dict shape the snapshot
    builder expects. Centralised so batch and per-agent paths agree."""
    return {
        "agent_id": fc.agent_id,
        "provider": fc.provider,
        "hours_to_shortage": fc.hours_to_shortage,
        "confidence": fc.confidence,
        "summary": fc.summary or "",
        "reasons_json": fc.reasons_json or "[]",
        "feature_importance_json": fc.feature_importance_json or "{}",
        "data_quality": fc.data_quality if fc.data_quality is not None else 1.0,
        "ts": fc.ts,
    }


def batch_agent_snapshots(session: Session, agent_ids: List[int]) -> List[dict]:
    """Build snapshot dicts for many agents in 4 batch queries.

    Returns a list of dicts in the same shape as ``agent_snapshot`` — one
    per agent_id in agent_ids. Agent ids that don't resolve to a real
    Agent row are skipped (no empty placeholder dict).
    """
    if not agent_ids:
        return []

    # --- 1. Agents in one shot --------------------------------------------
    agents = session.exec(select(Agent).where(Agent.id.in_(agent_ids))).all()
    agents_by_id: Dict[int, Agent] = {a.id: a for a in agents}
    if not agents_by_id:
        return []

    # --- 2. Provider balances in one shot ---------------------------------
    balances_rows = session.exec(
        select(ProviderBalance).where(ProviderBalance.agent_id.in_(agent_ids))
    ).all()
    balances_by_agent: Dict[int, Dict[str, float]] = {}
    for pb in balances_rows:
        balances_by_agent.setdefault(pb.agent_id, {})[pb.provider] = pb.balance

    # --- 3. Balance history: pull the last N rows per (agent, provider) ---
    # We can't use a window function with sqlmodel/sqlite in a portable way,
    # so we filter by agent_id set and then trim in Python. This still
    # collapses 3*len(agents) round trips into 1.
    history_rows = session.exec(
        select(BalanceHistory)
        .where(BalanceHistory.agent_id.in_(agent_ids))
        .order_by(BalanceHistory.ts.desc())
    ).all()
    # group → [(agent_id, provider)] → list[balance] (newest → oldest)
    history_by_key: Dict[tuple, List[float]] = {}
    for h in history_rows:
        key = (h.agent_id, h.provider)
        bucket = history_by_key.setdefault(key, [])
        if len(bucket) < _HISTORY_ROWS_PER_PROVIDER:
            bucket.append(h.balance)
    # Reverse to oldest → newest so callers can slice / tail naturally
    for k in history_by_key:
        history_by_key[k].reverse()

    # --- 4. Forecast snapshots: latest per (agent, provider) ---------------
    # Pull a recent window (last 200 per agent-provider pair would be
    # wasteful) — for the prototype the easiest correct strategy is to
    # fetch the latest few rows per agent_id and keep the newest per
    # provider. A single WHERE agent_id IN (...) ORDER BY ts DESC LIMIT N
    # keeps this O(1) round-trips.
    forecast_rows = session.exec(
        select(ForecastSnapshot)
        .where(ForecastSnapshot.agent_id.in_(agent_ids))
        .order_by(ForecastSnapshot.ts.desc())
        .limit(len(agent_ids) * 8)  # 4 providers × 2 most-recent = plenty
    ).all()
    forecasts_by_key: Dict[tuple, dict] = {}
    for fc in forecast_rows:
        key = (fc.agent_id, fc.provider)
        if key not in forecasts_by_key:
            forecasts_by_key[key] = _forecast_dict(fc)

    # --- 5. Alerts: latest per agent --------------------------------------
    alerts_rows = session.exec(
        select(Alert)
        .where(Alert.agent_id.in_(agent_ids))
        .order_by(Alert.created_at.desc())
        .limit(len(agent_ids) * _ALERTS_PER_AGENT)
    ).all()
    alerts_by_agent: Dict[int, List[Alert]] = {}
    for a in alerts_rows:
        bucket = alerts_by_agent.setdefault(a.agent_id, [])
        if len(bucket) < _ALERTS_PER_AGENT:
            bucket.append(a)

    # --- assemble per-agent dicts ------------------------------------------
    out: List[dict] = []
    for agent_id in agent_ids:
        agent = agents_by_id.get(agent_id)
        if agent is None:
            continue
        balances = balances_by_agent.get(agent_id, {})
        open_alerts = alerts_by_agent.get(agent_id, [])

        history_by_provider: Dict[str, List[float]] = {p: [] for p in PROVIDERS}
        for prov in PROVIDERS:
            history_by_provider[prov] = list(history_by_key.get((agent_id, prov), []))

        forecasts: Dict[str, Optional[dict]] = {}
        providers_out = []
        for prov in list(PROVIDERS) + ["physical"]:
            fc = forecasts_by_key.get((agent_id, prov))
            forecasts[prov] = fc
            try:
                reasons = json.loads(fc["reasons_json"]) if fc else []
            except Exception:
                reasons = []
            from ..services.liquidity import rate_projection
            rp = rate_projection(session, agent_id, prov)
            history = history_by_provider.get(prov, [])[-20:]
            hours_to_shortage = fc["hours_to_shortage"] if fc else None
            expected_outflow = _expected_outflow(rp.burn_rate_per_min)
            providers_out.append({
                "provider": prov,
                "balance": balances.get(prov, 0.0),
                "health": _provider_health(balances.get(prov, 0.0), hours_to_shortage,
                                          fc["data_quality"] if fc else 1.0),
                "burn_rate_per_min": rp.burn_rate_per_min,
                "hours_to_shortage": hours_to_shortage,
                "forecast_confidence": fc["confidence"] if fc else 0.0,
                "forecast_summary": (fc["summary"] if fc else "") or (reasons[0] if reasons else ""),
                "forecast_reasons": reasons,
                "data_quality": fc["data_quality"] if fc else 1.0,
                "history": history,
                "recent_deltas": _recent_deltas(history, 3),
                "expected_outflow_next_hours": expected_outflow,
                "current_demand_label": _demand_label(expected_outflow, rp.burn_rate_per_min),
                "shortage_eta_human": _shortage_eta_human(hours_to_shortage),
            })

        dq_by_provider: Dict[str, float] = {
            p.get("provider"): p.get("data_quality", 1.0)
            for p in providers_out
            if isinstance(p, dict) and p.get("provider")
        }
        # Pass-through: convert the forecast dict shape to a small object
        # so overall_score() keeps working (it only reads hours_to_shortage
        # and data_quality).
        forecast_objs: Dict[str, object] = {}
        for prov, fc in forecasts.items():
            if fc is None:
                forecast_objs[prov] = None
                continue

            class _FcShim:
                pass

            shim = _FcShim()
            shim.hours_to_shortage = fc["hours_to_shortage"]
            shim.data_quality = fc["data_quality"]
            forecast_objs[prov] = shim
        overall, overall_reason = overall_score(balances, forecast_objs, dq_by_provider)

        # ---- Combined pool block (same formula as agent_snapshot) ---------
        physical = float(balances.get("physical", 0.0))
        total_emoney = 0.0
        weighted_burn_num = 0.0
        weighted_burn_den = 0.0
        weighted_conf_num = 0.0
        weighted_conf_den = 0.0
        worst_dq = 1.0
        n_with_burn = 0
        n_with_h2s = 0
        for prov_block in providers_out:
            dq = float(prov_block.get("data_quality") or 0.0)
            burn = float(prov_block.get("burn_rate_per_min") or 0.0)
            conf = float(prov_block.get("forecast_confidence") or 0.0)
            bal = float(prov_block.get("balance") or 0.0)
            total_emoney += bal
            if burn > 0:
                weighted_burn_num += burn * max(dq, 0.05)
                weighted_burn_den += max(dq, 0.05)
                n_with_burn += 1
            if prov_block.get("hours_to_shortage") is not None:
                n_with_h2s += 1
            weighted_conf_num += max(min(conf, 1.0), 0.0) * max(min(dq, 1.0), 0.0)
            weighted_conf_den += 1.0
            worst_dq = min(worst_dq, dq)

        combined_burn_per_min = (
            weighted_burn_num / weighted_burn_den if weighted_burn_den > 0 else 0.0
        )
        combined_confidence = (
            weighted_conf_num / weighted_conf_den if weighted_conf_den > 0 else 0.0
        )
        total_cash = physical + total_emoney

        combined_hours_to_shortage: Optional[float]
        notes: List[str] = []
        if total_cash <= 0:
            combined_hours_to_shortage = 0.0
            notes.append("Combined pool is empty — refill or wait for incoming transactions.")
        elif combined_burn_per_min <= 0 or n_with_burn == 0:
            combined_hours_to_shortage = None
            notes.append("Not enough burn-rate samples yet — need a few minutes of activity to project the shared pool.")
        elif combined_confidence < 0.25:
            combined_hours_to_shortage = None
            notes.append("Combined projection paused — confidence is low because some provider feeds are late or stale.")
        elif worst_dq < 0.4:
            combined_hours_to_shortage = None
            notes.append("Combined projection paused — at least one provider's data quality is below the safe threshold.")
        else:
            combined_hours_to_shortage = round(total_cash / (combined_burn_per_min * 60.0), 2)

        if combined_hours_to_shortage is None:
            healthy_label = "projection unavailable right now"
            can_serve_hours_text = "—"
        elif combined_hours_to_shortage < 0.5:
            healthy_label = "combined pool will run out in under 30 minutes"
            can_serve_hours_text = f"{int(round(combined_hours_to_shortage * 60))} min"
        elif combined_hours_to_shortage < 2:
            healthy_label = "combined pool will run out in under 2 hours"
            can_serve_hours_text = f"{combined_hours_to_shortage:.1f} hours"
        elif combined_hours_to_shortage < 6:
            healthy_label = "comfortable for the next few hours"
            can_serve_hours_text = f"{combined_hours_to_shortage:.1f} hours"
        else:
            healthy_label = "comfortable — well over half a day of activity"
            can_serve_hours_text = f"{combined_hours_to_shortage:.1f} hours"

        if combined_confidence > 0:
            if combined_confidence >= 0.6:
                notes.append(f"Confidence is high ({int(round(combined_confidence * 100))}%) — projection is reliable.")
            elif combined_confidence >= 0.3:
                notes.append(f"Confidence is moderate ({int(round(combined_confidence * 100))}%) — treat the projection as advisory.")
            else:
                notes.append(f"Confidence is low ({int(round(combined_confidence * 100))}%) — wait for more data before acting on the number.")

        combined_block = {
            "total_cash": round(total_cash, 2),
            "physical_cash": round(physical, 2),
            "total_emoney": round(total_emoney, 2),
            "combined_burn_per_min": round(combined_burn_per_min, 4),
            "hours_to_shortage": combined_hours_to_shortage,
            "shortage_eta_human": _shortage_eta_human(combined_hours_to_shortage),
            "confidence": round(combined_confidence, 3),
            "data_quality": round(worst_dq, 3),
            "healthy_label": healthy_label,
            "can_serve_hours_text": can_serve_hours_text,
            "providers_with_burn_signal": n_with_burn,
            "providers_with_shortage_projection": n_with_h2s,
            "fallback_active": combined_hours_to_shortage is None,
            "notes": notes,
        }

        out.append({
            "agent_id": agent.id,
            "agent_code": agent.code,
            "display_name": agent.display_name,
            "area": agent.area,
            "physical_cash": physical,
            "overall_score": overall,
            "overall_reason": overall_reason,
            "providers": providers_out,
            "combined": combined_block,
            "alerts": [
                {
                    "id": a.id,
                    "provider": a.provider,
                    "severity": a.severity,
                    "priority_score": a.priority_score,
                    "title": a.title,
                    "summary": a.summary,
                    "confidence": a.confidence,
                    "status": a.status,
                    "owner_role": a.owner_role,
                    "owner_label": a.owner_label,
                    "created_at": a.created_at.isoformat(),
                }
                for a in open_alerts
            ],
        })
    return out