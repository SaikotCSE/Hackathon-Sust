"""Module 2 — AI Liquidity Prediction Engine.

Depletion-rate projection (primary, always computed) + LightGBM (secondary
confirmation only). Same 'never let one model be the sole voice' pattern that
Module 3 applies with Rule Engine + Isolation Forest.
"""
from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from statistics import pstdev
from typing import Deque, Dict, List, Optional, Tuple

from sqlmodel import Session, select

from ..models.database import BalanceHistory, ForecastSnapshot


# ---------------------------------------------------------------------------
# Primary: depletion-rate projection (always available)
# ---------------------------------------------------------------------------

@dataclass
class RateProjection:
    hours_to_shortage: Optional[float]
    confidence: float
    burn_rate_per_min: float
    variance: float
    reasons: List[str]
    window_minutes: int


def _select_window_minutes(provider: str) -> int:
    # 60-minute rolling window for burn rate is a good prototype default
    return 60


def rate_projection(
    session: Session, agent_id: int, provider: str, *, now: Optional[datetime] = None
) -> RateProjection:
    now = now or datetime.utcnow()
    window = _select_window_minutes(provider)
    window_start = now - timedelta(minutes=window)

    # Use balance-history points and aggregate outflows from BalanceHistory deltas.
    history = session.exec(
        select(BalanceHistory)
        .where(BalanceHistory.agent_id == agent_id)
        .where(BalanceHistory.provider == provider)
        .where(BalanceHistory.ts >= window_start)
        .order_by(BalanceHistory.ts)
    ).all()

    physical = [
        h for h in session.exec(
            select(BalanceHistory)
            .where(BalanceHistory.agent_id == agent_id)
            .where(BalanceHistory.provider == "physical")
            .where(BalanceHistory.ts >= window_start)
            .order_by(BalanceHistory.ts)
        ).all()
    ]

    if len(history) < 3:
        return RateProjection(
            hours_to_shortage=None,
            confidence=0.30,
            burn_rate_per_min=0.0,
            variance=0.0,
            reasons=["not enough history yet — need a few minutes of burn-rate samples"],
            window_minutes=window,
        )

    # Compute the deltas between consecutive history points (cash-out equivalents: drops)
    points: List[Tuple[datetime, float]] = [(h.ts, h.balance) for h in history]
    drops: List[float] = []
    per_min_rates: List[float] = []
    # Sub-minute intervals indicate the simulator wrote multiple history points
    # inside one logical minute (e.g. several ticks per second). Clamping intervals
    # to a sane minimum prevents 1-second intervals from producing astronomical
    # burn rates — without this guard, a single outlier second dominates the
    # whole 60-minute window.
    MIN_INTERVAL_MIN = 0.5
    for i in range(1, len(points)):
        prev_ts, prev_bal = points[i - 1]
        cur_ts, cur_bal = points[i]
        mins = max(MIN_INTERVAL_MIN, (cur_ts - prev_ts).total_seconds() / 60.0)
        delta = prev_bal - cur_bal  # positive = outflow
        # Negative deltas are inflows (cash-in / commission). For *shortage* projection
        # we treat inflows as a pause in burn — clip to 0.
        if delta > 0:
            per_min_rates.append(delta / mins)
        else:
            per_min_rates.append(0.0)

    if not per_min_rates or sum(per_min_rates) <= 0:
        return RateProjection(
            hours_to_shortage=None,
            confidence=0.40,
            burn_rate_per_min=0.0,
            variance=0.0,
            reasons=["no outflow detected in window — provider wallet is stable or being rebalanced"],
            window_minutes=window,
        )

    # Outlier-resistant estimator: a single anomaly-injected huge outflow
    # in the window (e.g. 100,000 BDT in one minute) would otherwise
    # dominate the naive mean and project "~0 min to shortage" for a wallet
    # that actually still has hours of buffer. Use median + cap, and use
    # pstdev on the capped series so the confidence score reflects the
    # burn rate the agent actually experiences.
    sorted_rates = sorted(per_min_rates)
    median_rate = sorted_rates[len(sorted_rates) // 2]
    # Cap any per-minute rate at 10x the median — anything beyond that is
    # almost certainly an anomaly event, not normal customer traffic.
    BURN_CAP_MULTIPLIER = 10.0
    cap = max(median_rate * BURN_CAP_MULTIPLIER, 1.0)
    capped_rates = [min(r, cap) for r in per_min_rates]
    avg_rate = sum(capped_rates) / len(capped_rates)
    variance = pstdev(capped_rates) if len(capped_rates) > 1 else 0.0
    cv = (variance / avg_rate) if avg_rate > 0 else 1.0
    # tighter variance ⇒ higher confidence
    confidence = max(0.30, min(0.95, 0.95 - min(0.65, cv)))

    last_balance = points[-1][1]
    if last_balance <= 0:
        return RateProjection(
            hours_to_shortage=0.0,
            confidence=min(0.99, confidence + 0.05),
            burn_rate_per_min=avg_rate,
            variance=variance,
            reasons=["balance already at or below zero — operational triage required"],
            window_minutes=window,
        )

    minutes_left = last_balance / max(avg_rate, 1e-6)
    hours_left = minutes_left / 60.0

    reasons: List[str] = []
    n_capped = sum(1 for r in per_min_rates if r > cap)
    if n_capped > 0:
        reasons.append(
            f"capped {n_capped} outlier minute(s) above {cap:.0f} BDT/min to keep the projection honest"
        )
    if avg_rate > 0:
        reasons.append(f"average burn rate {avg_rate:.0f} BDT/min over the last {window} min")
    if variance > 0:
        reasons.append(f"burn-rate stdev {variance:.0f} BDT/min (cv {cv:.2f})")
    if last_balance < 15_000:
        reasons.append(f"low remaining balance {last_balance:,.0f} BDT")
    if avg_rate == 0:
        reasons.append("no recent outflow — projection paused")

    return RateProjection(
        hours_to_shortage=hours_left if minutes_left > 0 else None,
        confidence=confidence,
        burn_rate_per_min=avg_rate,
        variance=variance,
        reasons=reasons or [f"balance {last_balance:,.0f} BDT, burn {avg_rate:.0f} BDT/min"],
        window_minutes=window,
    )


# ---------------------------------------------------------------------------
# Secondary: LightGBM confirmation (only when enough history)
# ---------------------------------------------------------------------------

_LGBM_CACHE: Dict[Tuple[int, str], Dict] = {}


@dataclass
class LGBMResult:
    hours_to_shortage: Optional[float]
    confidence: float
    feature_importance: Dict[str, float]


def _lgbm_enabled() -> bool:
    try:
        import lightgbm  # noqa: F401
        return True
    except Exception:
        return False


def _lgbm_suppressed(reason: str) -> LGBMResult:
    return LGBMResult(hours_to_shortage=None, confidence=0.0,
                      feature_importance={"_suppressed": 1.0, "_reason": reason})


def lgbm_predict(
    session: Session, agent_id: int, provider: str, projection: RateProjection
) -> LGBMResult:
    """Train per-provider on balance history; predict log(hours+1) for next point.

    Returns a suppressed result when there isn't enough history (cold start) or the
    provider's feed is degraded, so the caller can decide whether to surface it.
    """
    if not _lgbm_enabled():
        return _lgbm_suppressed("lightgbm not installed")

    snapshots = session.exec(
        select(BalanceHistory)
        .where(BalanceHistory.agent_id == agent_id)
        .where(BalanceHistory.provider == provider)
        .order_by(BalanceHistory.ts)
    ).all()

    if len(snapshots) < 20:
        return _lgbm_suppressed(f"only {len(snapshots)} snapshots — need ~20 for stable fit")

    try:
        import numpy as np
        import lightgbm as lgb
    except Exception as exc:  # pragma: no cover — defensive
        return _lgbm_suppressed(f"import error: {exc}")

    feats: List[List[float]] = []
    targets: List[float] = []
    ts_list: List[datetime] = []
    balances: List[float] = []

    for h in snapshots:
        ts_list.append(h.ts)
        balances.append(h.balance)

    # Build features per snapshot: rolling outflow velocity, variance, time-of-day,
    # balance ratio, provider one-hot (only one provider is in scope here).
    window = 5
    for i in range(window, len(snapshots) - 1):
        prev = balances[i] - balances[i - window]
        recent_rates = [max(0.0, balances[j] - balances[j - 1]) for j in range(max(1, i - window), i)]
        var = float(np.var(recent_rates)) if recent_rates else 0.0
        v = sum(recent_rates) / max(1, len(recent_rates))
        cur_bal = balances[i]
        max_bal_so_far = max(balances[: i + 1]) or 1.0
        balance_ratio = cur_bal / max_bal_so_far
        ts = ts_list[i]
        tod = ts.hour + ts.minute / 60.0
        is_festival = 0.0  # prototype: no festival flag in historical data
        feats.append([v, var, tod, balance_ratio, 1.0 if provider == "bkash" else 0.0,
                      1.0 if provider == "nagad" else 0.0, 1.0 if provider == "rocket" else 0.0])
        # Target: ratio of (current balance / max balance) — proxy for shortage likelihood
        targets.append(balance_ratio)

    if len(feats) < 5:
        return _lgbm_suppressed(f"only {len(feats)} feature rows — not enough to fit")

    X = np.array(feats, dtype=np.float64)
    y = np.array(targets, dtype=np.float64)
    feature_names = ["rolling_outflow_velocity", "rate_variance", "time_of_day",
                     "balance_ratio_to_max", "is_bkash", "is_nagad", "is_rocket"]

    cache_key = (agent_id, provider)
    model_entry = _LGBM_CACHE.get(cache_key)
    if model_entry is None or model_entry["n"] != len(snapshots):
        train_data = lgb.Dataset(X, label=y, feature_name=feature_names)
        params = {
            "objective": "regression",
            "metric": "rmse",
            "learning_rate": 0.05,
            "num_leaves": 8,
            "min_data_in_leaf": 4,
            "verbose": -1,
        }
        booster = lgb.train(params, train_data, num_boost_round=40)
        importance = booster.feature_importance(importance_type="gain")
        model_entry = {
            "booster": booster,
            "importance": dict(zip(feature_names, [float(x) for x in importance])),
            "n": len(snapshots),
        }
        _LGBM_CACHE[cache_key] = model_entry

    cur_bal = balances[-1]
    max_bal_so_far = max(balances) or 1.0
    cur_velocity = projection.burn_rate_per_min
    cur_var = projection.variance
    xq = np.array([[cur_velocity, cur_var, datetime.utcnow().hour, cur_bal / max_bal_so_far,
                    1.0 if provider == "bkash" else 0.0,
                    1.0 if provider == "nagad" else 0.0,
                    1.0 if provider == "rocket" else 0.0]], dtype=np.float64)
    ratio_pred = float(model_entry["booster"].predict(xq)[0])
    ratio_pred = max(0.01, min(1.0, ratio_pred))

    # Convert predicted ratio → hours-to-shortage (assumes linear continuation of burn)
    if projection.burn_rate_per_min > 0:
        hours_pred = (cur_bal * ratio_pred) / max(projection.burn_rate_per_min * 60.0, 1.0)
    else:
        hours_pred = None

    return LGBMResult(
        hours_to_shortage=hours_pred,
        confidence=0.65,
        feature_importance=model_entry["importance"],
    )


# ---------------------------------------------------------------------------
# Combined forecast writer
# ---------------------------------------------------------------------------

def compute_forecast(
    session: Session, agent_id: int, provider: str, *, data_quality: float = 1.0
) -> ForecastSnapshot:
    """Always-runs: primary rate projection + optional LightGBM confirmation."""
    primary = rate_projection(session, agent_id, provider)

    if data_quality < 0.5:
        # Force LightGBM out of the picture on degraded feed
        lgbm = LGBMResult(hours_to_shortage=None, confidence=0.0,
                          feature_importance={"_suppressed": 1.0, "_reason": "data-quality degraded"})
        method = "rate_projection"
    else:
        lgbm = lgbm_predict(session, agent_id, provider, primary)

    hours = primary.hours_to_shortage
    conf = primary.confidence
    method = "rate_projection"
    reasons = list(primary.reasons)
    importance: Dict[str, float] = {}

    lgbm_suppressed = (lgbm.feature_importance.get("_suppressed", 0.0) >= 1.0)
    if not lgbm_suppressed and lgbm.hours_to_shortage is not None and hours is not None:
        # Agreement check — be tolerant because LightGBM is fitted on noisy demo data.
        lo, hi = min(primary.hours_to_shortage, lgbm.hours_to_shortage), max(primary.hours_to_shortage, lgbm.hours_to_shortage)
        ratio = (hi / lo) if lo > 0 else float("inf")
        if ratio < 2.0:
            conf = min(0.97, primary.confidence + 0.05)
            method = "rate_projection+lgbm"
            importance = {k: v for k, v in lgbm.feature_importance.items() if not k.startswith("_")}
            # Surface top feature-importance reason
            if importance:
                top = max(importance, key=importance.get)
                reasons.append(f"LightGBM confirmation — top contributor: {top}")
        else:
            # Disagreement: silently drop LightGBM
            pass

    snap = ForecastSnapshot(
        agent_id=agent_id,
        provider=provider,
        hours_to_shortage=hours,
        confidence=conf * data_quality,  # degrade confidence on bad feed
        reasons_json=json.dumps(reasons),
        method=method,
        feature_importance_json=json.dumps(importance),
        data_quality=data_quality,
    )
    session.add(snap)
    session.commit()
    session.refresh(snap)
    return snap


# ---------------------------------------------------------------------------
# JSON helper (must be at bottom; defined late to avoid circular import)
# ---------------------------------------------------------------------------
import json  # noqa: E402