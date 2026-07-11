"""Module 2 — AI Liquidity Prediction Engine.

Depletion-rate projection (primary, always computed) + LightGBM (secondary
confirmation only). Same 'never let one model be the sole voice' pattern that
Module 3 applies with Rule Engine + Isolation Forest.
"""
from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from statistics import median, pstdev
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
        .order_by(BalanceHistory.ts.desc())
        .limit(_RATE_PROJ_HISTORY_LIMIT)
    ).all()
    history = list(reversed(history))

    # Cold-start fallback: if the rolling window is empty but we *do* have
    # samples since the agent first started, use that window instead of
    # returning nothing. This avoids the "confidence 30% not enough history"
    # state on a freshly-ticked provider that just hasn't accumulated 60 min yet.
    if len(history) < 5:
        all_history = session.exec(
            select(BalanceHistory)
            .where(BalanceHistory.agent_id == agent_id)
            .where(BalanceHistory.provider == provider)
            .order_by(BalanceHistory.ts.desc())
            .limit(_RATE_PROJ_HISTORY_LIMIT)
        ).all()
        all_history = list(reversed(all_history))
        if len(all_history) >= 5:
            history = all_history
            window = max(1, int((history[-1].ts - history[0].ts).total_seconds() / 60))

    if len(history) < 5:
        return RateProjection(
            hours_to_shortage=None,
            confidence=0.0,
            burn_rate_per_min=0.0,
            variance=0.0,
            reasons=[f"insufficient data — only {len(history)} samples; need at least 5"],
            summary="insufficient data — projection unavailable",
            window_minutes=window,
        )

    # Collapse exact duplicate timestamps. Different balances at the same instant
    # are contradictory observations, not an ultra-fast drain signal.
    grouped: Dict[datetime, List[float]] = defaultdict(list)
    for h in history:
        grouped[h.ts].append(float(h.balance))
    conflict_times = [ts for ts, vals in grouped.items() if max(vals) - min(vals) > max(1.0, median(vals) * 0.01)]
    # A single old overlap (for example an hourly and minute seed landing on
    # the same boundary) is recoverable: discard it and lower confidence. A
    # conflicted latest observation or a materially conflicted feed is not.
    if conflict_times and (max(conflict_times) == max(grouped) or len(conflict_times) / len(grouped) >= 0.2):
        return RateProjection(
            hours_to_shortage=None, confidence=0.0, burn_rate_per_min=0.0,
            variance=0.0,
            reasons=[f"conflicting data — {len(conflict_times)} timestamp(s) report different balances"],
            summary="conflicting data — projection paused", window_minutes=window,
        )
    points: List[Tuple[datetime, float]] = sorted(
        ((ts, sum(vals) / len(vals)) for ts, vals in grouped.items() if ts not in conflict_times), key=lambda p: p[0]
    )
    if len(points) < 5:
        return RateProjection(None, 0.0, 0.0, 0.0,
                              ["insufficient distinct timestamps to estimate a trend"],
                              "insufficient data — projection unavailable", window)

    intervals = [(points[i][0] - points[i - 1][0]).total_seconds() / 60.0 for i in range(1, len(points))]
    if any(v <= 0 for v in intervals):
        return RateProjection(None, 0.0, 0.0, 0.0, ["timestamps are not strictly increasing"],
                              "conflicting data — projection paused", window)
    typical_interval = median(intervals)
    age_min = max(0.0, (now - points[-1][0]).total_seconds() / 60.0)
    stale_after = max(5.0, typical_interval * 3.0)
    if age_min > stale_after:
        return RateProjection(None, 0.0, 0.0, 0.0,
                              [f"latest balance is {age_min:.0f} min old — feed is stale"],
                              "stale data — projection paused", window)
    span_min = (points[-1][0] - points[0][0]).total_seconds() / 60.0
    if span_min < 3.0:
        return RateProjection(None, 0.2, 0.0, 0.0,
                              [f"insufficient time span — {span_min:.1f} min observed; need at least 3 min"],
                              "insufficient data — projection unavailable", window)

    def trend(sample: List[Tuple[datetime, float]]) -> Tuple[float, float, float]:
        """Return net burn/min, residual stddev and R² from a balance trend."""
        origin = sample[0][0]
        xs = [(ts - origin).total_seconds() / 60.0 for ts, _ in sample]
        ys = [bal for _, bal in sample]
        xbar, ybar = sum(xs) / len(xs), sum(ys) / len(ys)
        denom = sum((x - xbar) ** 2 for x in xs)
        slope = sum((x - xbar) * (y - ybar) for x, y in zip(xs, ys)) / max(denom, 1e-9)
        fitted = [ybar + slope * (x - xbar) for x in xs]
        residuals = [y - fit for y, fit in zip(ys, fitted)]
        residual_sd = pstdev(residuals) if len(residuals) > 1 else 0.0
        total_ss = sum((y - ybar) ** 2 for y in ys)
        residual_ss = sum(r * r for r in residuals)
        r2 = 1.0 - residual_ss / total_ss if total_ss > 1e-9 else 1.0
        return max(0.0, -slope), residual_sd, max(0.0, min(1.0, r2))

    avg_rate, variance, r2 = trend(points)
    recent = [p for p in points if (points[-1][0] - p[0]).total_seconds() <= 15 * 60]
    if len(recent) >= 5 and (recent[-1][0] - recent[0][0]).total_seconds() >= 3 * 60:
        recent_rate, recent_variance, recent_r2 = trend(recent)
        # React to a sustained sudden drain instead of hiding it in an hour average.
        if recent_rate > avg_rate * 1.5 and recent_r2 >= 0.5:
            avg_rate, variance, r2 = recent_rate, recent_variance, recent_r2
            window = max(3, int((recent[-1][0] - recent[0][0]).total_seconds() / 60.0))

    if avg_rate <= 1e-9:
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

    expected_count = max(1.0, span_min / max(typical_interval, 1e-6) + 1.0)
    coverage = min(1.0, len(points) / expected_count)
    large_gaps = sum(1 for gap in intervals if gap > typical_interval * 3.0)
    confidence = min(0.95, max(0.2, 0.35 + 0.45 * r2 + 0.15 * coverage))
    confidence *= max(0.4, 1.0 - large_gaps * 0.12)
    confidence *= max(0.5, 1.0 - len(conflict_times) * 0.15)

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
    if large_gaps:
        reasons.append(f"reduced confidence for {large_gaps} missing/late interval(s)")
    if conflict_times:
        reasons.append(f"reduced confidence after discarding {len(conflict_times)} conflicting timestamp(s)")
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
        reasons=reasons or [f"balance {last_balance:,.0f} BDT, net burn {avg_rate:.0f} BDT/min"],
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
        recent_rates = [max(0.0, balances[j - 1] - balances[j]) for j in range(max(1, i - window), i)]
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

    if data_quality < 0.5:
        hours = None
        conf = min(conf, 0.2) * max(data_quality, 0.0)
        reasons.append(f"projection withheld — data quality {data_quality:.2f} is below safe threshold")
        primary.summary = "feed degraded — projection unavailable"

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

    final_confidence = conf * dq_penalty
    if hours is not None and hours > 0 and final_confidence < 0.5:
        reasons.append(
            f"depletion time withheld — confidence {final_confidence:.2f} is below the 0.50 decision threshold"
        )
        hours = None
        primary.summary = "low-confidence trend — monitor until more data arrives"

    snap = ForecastSnapshot(
        agent_id=agent_id,
        provider=provider,
        hours_to_shortage=hours,
        confidence=final_confidence,
        summary=primary.summary,  # curated one-line basis for UI / alert copy
        reasons_json=json.dumps(reasons),
        method=method,
        feature_importance_json=json.dumps(importance),
        data_quality=data_quality,
        burn_rate_per_min=primary.burn_rate_per_min,
    )
    session.add(snap)
    session.commit()
    session.refresh(snap)
    return snap


# ---------------------------------------------------------------------------
# JSON helper (must be at bottom; defined late to avoid circular import)
# ---------------------------------------------------------------------------
import json  # noqa: E402
