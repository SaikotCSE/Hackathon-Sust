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


def overall_score(balances: Dict[str, float], forecasts: Dict[str, Optional[ForecastSnapshot]]) -> tuple:
    """0..100. Lower = healthier. Each provider contributes its burn-rate-to-threshold."""
    if not balances:
        return 80, "no provider data yet"
    score = 0
    weight = 0
    reasons = []
    for prov, bal in balances.items():
        fc = forecasts.get(prov)
        if fc is None or fc.hours_to_shortage is None:
            score += 90  # healthy bucket
            weight += 1
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
    agent = session.get(Agent, agent_id)
    if agent is None:
        return {}

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
    for prov in PROVIDERS:
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

    overall, overall_reason = overall_score(balances, forecasts)

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