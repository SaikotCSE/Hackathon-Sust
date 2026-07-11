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

# LightGBM model cache TTL — retrain at most once per this many seconds
# even if new balance_history rows arrive. The model is fit on a 60-min
# burn-rate proxy, so refreshing every minute matches the signal window.
# Was 0 (effectively invalidated on every snapshot row → retrain every tick).
_LGBM_CACHE_TTL_SECONDS = 60


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
    summary: str                # curated one-line basis for the UI basis line
    window_minutes: int


def _select_window_minutes(provider: str) -> int:
    # 60-minute rolling window for burn rate is a good prototype default.
    # We also try a "since-first-sample" fallback for cold start — handled
    # inside rate_projection.
    return 60


def rate_projection(
    session: Session, agent_id: int, provider: str, *, now: Optional[datetime] = None
) -> RateProjection:
    now = now or datetime.utcnow()
    window = _select_window_minutes(provider)
    window_start = now - timedelta(minutes=window)

    # Use balance-history points and aggregate outflows from BalanceHistory deltas.
    # Capped at the rolling window + a safety ceiling — without the cap a
    # provider that has been ticking for hours would load tens of thousands
    # of rows every time the dashboard polls.
    _RATE_PROJ_HISTORY_LIMIT = 120
    history = session.exec(
        select(BalanceHistory)
        .where(BalanceHistory.agent_id == agent_id)
        .where(BalanceHistory.provider == provider)
        .where(BalanceHistory.ts >= window_start)
        .order_by(BalanceHistory.ts)
        .limit(_RATE_PROJ_HISTORY_LIMIT)
    ).all()

    # Cold-start fallback: if the rolling window is empty but we *do* have
    # samples since the agent first started, use that window instead of
    # returning nothing. This avoids the "confidence 30% not enough history"
    # state on a freshly-ticked provider that just hasn't accumulated 60 min yet.
    if len(history) < 3:
        all_history = session.exec(
            select(BalanceHistory)
            .where(BalanceHistory.agent_id == agent_id)
            .where(BalanceHistory.provider == provider)
            .order_by(BalanceHistory.ts)
            .limit(_RATE_PROJ_HISTORY_LIMIT)
        ).all()
        if len(all_history) >= 3:
            history = all_history
            window = max(1, int((history[-1].ts - history[0].ts).total_seconds() / 60))

    if len(history) < 3:
        return RateProjection(
            hours_to_shortage=None,
            confidence=0.45,
            burn_rate_per_min=0.0,
            variance=0.0,
            reasons=["warming up — first few samples arriving, projection in ~1 min"],
            summary="warming up",
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
        # "No outflow" is a *good* state, not a low-confidence one. We are
        # confident the wallet is not draining — say so, and surface a high
        # confidence in "stable". A future negative delta will lower this.
        last_balance = points[-1][1]
        return RateProjection(
            hours_to_shortage=None,
            confidence=0.85,
            burn_rate_per_min=0.0,
            variance=0.0,
            reasons=["no outflow detected in window — wallet is stable or being rebalanced"],
            summary="stable — no draining",
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
    # tighter variance ⇒ higher confidence.
    # Floor raised to 0.55: a real burn signal that ran for ≥3 minutes
    # is meaningful, even if noisy. Ceiling kept at 0.95.
    confidence = max(0.55, min(0.95, 0.95 - min(0.40, cv)))

    last_balance = points[-1][1]
    if last_balance <= 0:
        return RateProjection(
            hours_to_shortage=0.0,
            confidence=min(0.97, confidence + 0.02),
            burn_rate_per_min=avg_rate,
            variance=variance,
            reasons=["balance already at or below zero — operational triage required"],
            summary="balance depleted — triage required",
            window_minutes=window,
        )

    minutes_left = last_balance / max(avg_rate, 1e-6)
    hours_left = minutes_left / 60.0

    # ---- Reasons (technical) ----
    reasons: List[str] = []
    n_capped = sum(1 for r in per_min_rates if r > cap)
    if n_capped > 0:
        reasons.append(
            f"capped {n_capped} outlier minute(s) above {cap:.0f} BDT/min to keep the projection honest"
        )
    if avg_rate > 0:
        reasons.append(f"average burn rate {avg_rate:.0f} BDT/min over the last {window} min")
    if last_balance < 15_000:
        reasons.append(f"low remaining balance {last_balance:,.0f} BDT")

    # ---- Summary (curated one-liner for the UI basis line) ----
    if hours_left < 1:
        summary = f"draining fast — about {int(round(hours_left * 60))} min of buffer left"
    elif hours_left < 6:
        summary = f"elevated burn — ~{fmt_duration_hours(hours_left)} until depletion"
    elif hours_left < 24:
        summary = f"steady burn — comfortable for {fmt_duration_hours(hours_left)}"
    else:
        summary = f"low burn — no shortage expected in the next {fmt_duration_hours(hours_left)}"

    return RateProjection(
        hours_to_shortage=hours_left if minutes_left > 0 else None,
        confidence=confidence,
        burn_rate_per_min=avg_rate,
        variance=variance,
        reasons=reasons or [f"balance {last_balance:,.0f} BDT, burn {avg_rate:.0f} BDT/min"],
        summary=summary,
        window_minutes=window,
    )


def fmt_duration_hours(hours: float) -> str:
    """Render hours as a short human-friendly string for summary messages."""
    total_min = int(round(hours * 60))
    if total_min < 60:
        return f"{total_min} min"
    h = total_min // 60
    m = total_min % 60
    if h < 24:
        return f"{h}h" if m == 0 else f"{h}h {m}m"
    d = h // 24
    rh = h % 24
    return f"{d}d" if rh == 0 else f"{d}d {rh}h"


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

    Cold-start policy:
      - Need ≥8 snapshots to attempt a fit (was 20). With 8–19 we still fit but
        flag the result as low-confidence context rather than a prediction.
      - With ≥20 snapshots we predict normally and the result counts as
        confirmation.
    In both cases, feature importances are always surfaced so the UI can use
    them as context ("LightGBM flags rolling_outflow_velocity as the dominant
    signal") even when no prediction is emitted.
    """
    if not _lgbm_enabled():
        return _lgbm_suppressed("lightgbm not installed")

    # Cap to the most recent N rows: enough history to fit a meaningful
    # model without loading the entire balance_history table per call.
    # Was unbounded — could load tens of thousands of rows after a long
    # demo session.
    _LGBM_HISTORY_LIMIT = 200
    snapshots = session.exec(
        select(BalanceHistory)
        .where(BalanceHistory.agent_id == agent_id)
        .where(BalanceHistory.provider == provider)
        .order_by(BalanceHistory.ts.desc())
        .limit(_LGBM_HISTORY_LIMIT)
    ).all()
    # Reverse to chronological order (oldest → newest) for the feature
    # builder below — the limit above gives us newest → newest, the
    # reversal keeps `points[i-1] ↔ points[i]` math intuitive.
    snapshots = list(reversed(snapshots))

    if len(snapshots) < 8:
        return _lgbm_suppressed(f"only {len(snapshots)} snapshots — need ~8 for a fit")

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

    if len(feats) < 4:
        return _lgbm_suppressed(f"only {len(feats)} feature rows — not enough to fit")

    X = np.array(feats, dtype=np.float64)
    y = np.array(targets, dtype=np.float64)
    feature_names = ["rolling_outflow_velocity", "rate_variance", "time_of_day",
                     "balance_ratio_to_max", "is_bkash", "is_nagad", "is_rocket"]

    cache_key = (agent_id, provider)
    now = datetime.utcnow()
    model_entry = _LGBM_CACHE.get(cache_key)
    # Refresh the cached model when:
    #   - no entry exists, OR
    #   - the cache TTL has elapsed (every 60s by default — the model is fit
    #     on a 60-min burn-rate proxy anyway), OR
    #   - the snapshot count crossed the 20-snapshot tier boundary (cold
    #     start → warmed). Without this check, a provider stuck at 12
    #     snapshots would never refresh past the lightweight params even if
    #     we later configured a different model.
    cache_stale = (
        model_entry is None
        or (now - model_entry["trained_at"]).total_seconds() > _LGBM_CACHE_TTL_SECONDS
        or (
            # tier boundary crossing: light (n<20) ↔ full (n>=20)
            (model_entry["n"] < 20 and len(snapshots) >= 20)
            or (model_entry["n"] >= 20 and len(snapshots) < 20)
        )
    )
    if cache_stale:
        # Lighter model for the smaller training set: shallower trees, fewer
        # leaves, more aggressive min_data_in_leaf to avoid overfitting to
        # 8–19 noisy points.
        n = len(snapshots)
        if n < 20:
            params = {
                "objective": "regression",
                "metric": "rmse",
                "learning_rate": 0.05,
                "num_leaves": 4,
                "min_data_in_leaf": max(2, n // 4),
                "verbose": -1,
            }
            num_boost_round = 20
        else:
            params = {
                "objective": "regression",
                "metric": "rmse",
                "learning_rate": 0.05,
                "num_leaves": 8,
                "min_data_in_leaf": 4,
                "verbose": -1,
            }
            num_boost_round = 40
        train_data = lgb.Dataset(X, label=y, feature_name=feature_names)
        booster = lgb.train(params, train_data, num_boost_round=num_boost_round)
        importance = booster.feature_importance(importance_type="gain")
        model_entry = {
            "booster": booster,
            "importance": dict(zip(feature_names, [float(x) for x in importance])),
            "n": len(snapshots),
            "n_features": len(feats),
            "trained_at": now,
        }
        _LGBM_CACHE[cache_key] = model_entry

    # Always surface feature importances — useful as context even when we
    # can't predict (cold start). Filter out non-signal columns.
    importance_clean = {
        k: v for k, v in model_entry["importance"].items()
        if not k.startswith("_") and not k.startswith("is_")  # drop one-hot cols
    }

    # Below 20 snapshots we don't trust the prediction enough to override
    # the rate projection — return importances as context only.
    if model_entry["n"] < 20:
        return LGBMResult(
            hours_to_shortage=None,
            confidence=0.0,
            feature_importance={
                **importance_clean,
                "_suppressed": 1.0,
                "_reason": f"only {model_entry['n']} snapshots — feature context only, no prediction",
            },
        )

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
        feature_importance=importance_clean,
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

    # When LightGBM is suppressed (cold start) but still produced feature
    # importances, surface the dominant signal as context-only — not as
    # confirmation. This way the UI can show "LightGBM flags
    # rolling_outflow_velocity" even with <20 snapshots.
    if lgbm_suppressed and not importance:
        context_only = {
            k: v for k, v in lgbm.feature_importance.items()
            if not k.startswith("_")
        }
        if context_only:
            top = max(context_only, key=context_only.get)
            importance = context_only
            # Subtle wording — never claim confirmation
            reasons.append(f"LightGBM context: {top} is the dominant signal")

    # Data-quality penalty — only kick in when the feed is actually degraded.
    # A healthy feed (DQ > 0.7) should not silently drop confidence from 0.85
    # to 0.74. Below 0.7 we taper the penalty linearly down to 0 at DQ=0.5.
    if data_quality >= 0.7:
        dq_penalty = 1.0
    elif data_quality > 0.5:
        dq_penalty = 0.5 + (data_quality - 0.5) / 0.4 * 0.5  # 0.5 → 1.0
    else:
        dq_penalty = 0.5  # floor — already short-circuited upstream

    snap = ForecastSnapshot(
        agent_id=agent_id,
        provider=provider,
        hours_to_shortage=hours,
        confidence=conf * dq_penalty,
        summary=primary.summary,  # curated one-line basis for UI / alert copy
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