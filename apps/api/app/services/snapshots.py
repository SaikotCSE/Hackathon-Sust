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


def _demand_label(expected_outflow: Optional[float], balance: float) -> str:
    """Map an outflow forecast onto a coarse low/medium/high bucket the UI can
    render as a single line on the provider card."""
    if expected_outflow is None or expected_outflow <= 0:
        return "low"
    ratio = expected_outflow / max(balance, 1.0)
    if ratio >= 1.0:
        return "high"
    if ratio >= 0.5:
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


def _expected_outflow(burn_rate_per_min: float, hours: float = 8.0) -> float:
    """Simple forward-looking outflow projection used for the 'Provider-Aware
    Demand' block. Falls back to zero when no burn rate is known."""
    return round(max(burn_rate_per_min, 0.0) * 60.0 * hours, 2)


def _forecast_display(fc, balance: float) -> tuple:
    """Return internally consistent ETA, burn, freshness and safe UI state."""
    now = datetime.utcnow()
    if fc is None:
        return None, 0.0, "unavailable", None, "No forecast has been computed yet."
    get = (lambda key, default=None: fc.get(key, default)) if isinstance(fc, dict) else (lambda key, default=None: getattr(fc, key, default))
    burn = max(0.0, float(get("burn_rate_per_min", 0.0) or 0.0))
    hours = get("hours_to_shortage")
    age = max(0.0, (now - get("ts")).total_seconds() / 60.0)
    confidence = float(get("confidence", 0.0) or 0.0)
    if age > 10:
        return None, burn, "stale", age, f"Forecast is {age:.0f} minutes old; run a new simulation tick."
    if balance <= 0:
        return 0.0, burn, "depleted", age, "Balance is depleted; verify and request support."
    if burn <= 0:
        return None, 0.0, "stable", age, "No net depletion trend in the latest window."
    if confidence < 0.5:
        return None, burn, "low_confidence", age, "Trend detected, but confidence is below 50%; monitor for more data."
    if hours is None:
        return None, burn, "insufficient_data", age, "Not enough reliable history for a depletion time."
    return float(hours), burn, "projected", age, get("summary", "") or "Trend-derived depletion estimate."


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


def operational_liquidity_summary(providers_out: List[dict]) -> dict:
    """Summarize the earliest independent constraint without pooling balances.

    Physical cash and each provider wallet remain separate positions. The
    aggregate pressure indicator is the earliest reliable depletion estimate
    among them, never a projection based on adding or converting balances.
    """
    physical = next((p for p in providers_out if p.get("provider") == "physical"), None)
    provider_rows = [p for p in providers_out if p.get("provider") != "physical"]
    incomplete = [p for p in providers_out if p.get("degraded")]
    candidates = [p for p in providers_out if p.get("hours_to_shortage") is not None]
    limiting = min(candidates, key=lambda p: float(p["hours_to_shortage"])) if candidates else None
    worst_dq = min((float(p.get("data_quality") or 0.0) for p in providers_out), default=0.0)

    if limiting is not None:
        hours = float(limiting["hours_to_shortage"])
        limiting_position = str(limiting.get("provider") or "unknown")
        confidence = float(limiting.get("forecast_confidence") or 0.0)
        if hours < 0.5:
            pressure_label = f"Critical: {limiting_position} is the earliest independent constraint"
        elif hours < 2:
            pressure_label = f"High pressure: {limiting_position} is the earliest independent constraint"
        elif hours < 6:
            pressure_label = f"Watch {limiting_position}: it is the earliest independent constraint"
        else:
            pressure_label = f"Earliest projected constraint is {limiting_position}"
    else:
        hours = None
        limiting_position = None
        confidence = min(
            (float(p.get("forecast_confidence") or 0.0) for p in providers_out),
            default=0.0,
        )
        pressure_label = (
            "Partial visibility: refresh incomplete positions before concluding liquidity is healthy"
            if incomplete
            else "No independent position is currently projected to deplete"
        )

    notes = [
        "Physical cash and provider e-money are independent, non-convertible positions.",
        "The aggregate pressure indicator uses the earliest position-level constraint; balances are never pooled.",
    ]
    if incomplete:
        names = ", ".join(str(p.get("provider")) for p in incomplete)
        notes.append(f"Partial visibility for: {names}; any displayed constraint is advisory.")

    return {
        "physical_cash": round(float((physical or {}).get("balance") or 0.0), 2),
        "provider_count": len(provider_rows),
        "limiting_position": limiting_position,
        "limiting_hours_to_shortage": hours,
        "shortage_eta_human": _shortage_eta_human(hours),
        "confidence": round(confidence, 3),
        "data_quality": round(worst_dq, 3),
        "pressure_label": pressure_label,
        "fallback_active": bool(incomplete) or (limiting is None and confidence < 0.5),
        "non_convertible": True,
        "notes": notes,
    }


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
        if fc is None:
            # NOT healthy — we just don't know. Surface as unknown so the
            # rollup can treat incomplete data differently from healthy.
            score += 60
            weight += 1
            no_signal.append(prov)
            reasons.append(f"{prov}: no forecast yet")
            continue
        fc_age = (datetime.utcnow() - fc.ts).total_seconds() / 60.0 if getattr(fc, "ts", None) else 0.0
        if fc_age > 10 or float(getattr(fc, "confidence", 1.0) or 0.0) < 0.5:
            score += 60
            weight += 1
            no_signal.append(prov)
            why = "stale" if fc_age > 10 else "low confidence"
            reasons.append(f"{prov}: {why} forecast — refresh before acting")
            continue
        if fc.hours_to_shortage is None:
            if str(getattr(fc, "summary", "")).startswith("stable"):
                score += 20
                weight += 1
                continue
            score += 60
            weight += 1
            no_signal.append(prov)
            reasons.append(f"{prov}: insufficient data for a forecast")
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

    history_by_provider: Dict[str, List[float]] = {p: [] for p in (*PROVIDERS, "physical")}
    for prov in (*PROVIDERS, "physical"):
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
        history = history_by_provider.get(prov, [])[-20:]
        balance = float(balances.get(prov, 0.0))
        hours_to_shortage, burn_rate, forecast_state, forecast_age, forecast_note = _forecast_display(fc, balance)
        expected_outflow = _expected_outflow(burn_rate)
        providers_out.append({
            "provider": prov,
            "balance": balance,
            "health": _provider_health(balance, hours_to_shortage, fc.data_quality if fc else 1.0),
            "burn_rate_per_min": burn_rate,
            "hours_to_shortage": hours_to_shortage,
            "forecast_confidence": fc.confidence if fc else 0.0,
            "forecast_summary": forecast_note,
            "forecast_reasons": reasons,
            "data_quality": fc.data_quality if fc else 1.0,
            "history": history,
            # ----- fields used by the role-aware dashboard card UI -----
            "recent_deltas": _recent_deltas(history, 3),
            "expected_outflow_next_hours": expected_outflow,
            "current_demand_label": _demand_label(expected_outflow, balance),
            "shortage_eta_human": _shortage_eta_human(hours_to_shortage),
            "forecast_state": forecast_state,
            "forecast_age_minutes": round(forecast_age, 1) if forecast_age is not None else None,
            "forecast_generated_at": fc.ts.isoformat() if fc else None,
            "projected_balance_8h": round(max(0.0, balance - expected_outflow), 2),
            "degraded": forecast_state in ("stale", "unavailable", "insufficient_data") or (fc.data_quality < 0.5 if fc else True),
            "degraded_reason": forecast_note if forecast_state in ("stale", "unavailable", "insufficient_data") else None,
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

    physical = float(balances.get("physical", 0.0))
    aggregate_block = operational_liquidity_summary(providers_out)

    return {
        "agent_id": agent.id,
        "agent_code": agent.code,
        "display_name": agent.display_name,
        "area": agent.area,
        "physical_cash": physical,
        "overall_score": overall,
        "overall_reason": overall_reason,
        "providers": providers_out,
        "aggregate": aggregate_block,
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
                "initial_owner": a.initial_owner,
                "reasons": json.loads(a.reasons_json or "[]"),
                "evidence": json.loads(a.evidence_json or "[]"),
                "recommended_actions": json.loads(a.recommended_actions_json or "[]"),
                "fused_explanation": a.fused_explanation,
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
        "burn_rate_per_min": getattr(fc, "burn_rate_per_min", 0.0),
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

        history_by_provider: Dict[str, List[float]] = {p: [] for p in (*PROVIDERS, "physical")}
        for prov in (*PROVIDERS, "physical"):
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
            history = history_by_provider.get(prov, [])[-20:]
            balance = float(balances.get(prov, 0.0))
            hours_to_shortage, burn_rate, forecast_state, forecast_age, forecast_note = _forecast_display(fc, balance)
            expected_outflow = _expected_outflow(burn_rate)
            providers_out.append({
                "provider": prov,
                "balance": balance,
                "health": _provider_health(balance, hours_to_shortage,
                                          fc["data_quality"] if fc else 1.0),
                "burn_rate_per_min": burn_rate,
                "hours_to_shortage": hours_to_shortage,
                "forecast_confidence": fc["confidence"] if fc else 0.0,
                "forecast_summary": forecast_note,
                "forecast_reasons": reasons,
                "data_quality": fc["data_quality"] if fc else 1.0,
                "history": history,
                "recent_deltas": _recent_deltas(history, 3),
                "expected_outflow_next_hours": expected_outflow,
                "current_demand_label": _demand_label(expected_outflow, balance),
                "shortage_eta_human": _shortage_eta_human(hours_to_shortage),
                "forecast_state": forecast_state,
                "forecast_age_minutes": round(forecast_age, 1) if forecast_age is not None else None,
                "forecast_generated_at": fc["ts"].isoformat() if fc else None,
                "projected_balance_8h": round(max(0.0, balance - expected_outflow), 2),
                "degraded": forecast_state in ("stale", "unavailable", "insufficient_data") or (fc["data_quality"] < 0.5 if fc else True),
                "degraded_reason": forecast_note if forecast_state in ("stale", "unavailable", "insufficient_data") else None,
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
            shim.confidence = fc["confidence"]
            shim.ts = fc["ts"]
            shim.summary = fc["summary"]
            forecast_objs[prov] = shim
        overall, overall_reason = overall_score(balances, forecast_objs, dq_by_provider)

        physical = float(balances.get("physical", 0.0))
        aggregate_block = operational_liquidity_summary(providers_out)

        out.append({
            "agent_id": agent.id,
            "agent_code": agent.code,
            "display_name": agent.display_name,
            "area": agent.area,
            "physical_cash": physical,
            "overall_score": overall,
            "overall_reason": overall_reason,
            "providers": providers_out,
            "aggregate": aggregate_block,
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
                    "initial_owner": a.initial_owner,
                    "reasons": json.loads(a.reasons_json or "[]"),
                    "evidence": json.loads(a.evidence_json or "[]"),
                    "recommended_actions": json.loads(a.recommended_actions_json or "[]"),
                    "fused_explanation": a.fused_explanation,
                    "created_at": a.created_at.isoformat(),
                }
                for a in open_alerts
            ],
        })
    return out
