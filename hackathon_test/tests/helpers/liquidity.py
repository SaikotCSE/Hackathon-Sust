"""Pure-Python reference implementations used by Layer 1 unit tests.

These functions model what the prototype's liquidity engine should do. The
prototype's actual implementation may live in any framework; these reference
helpers let us validate its behaviour deterministically without booting a
database. When the prototype exposes its own modules we can swap the imports.
"""
from __future__ import annotations

import math
import statistics
from dataclasses import dataclass
from typing import Iterable, List, Optional, Sequence

from .synth import SyntheticProviderFeed


@dataclass
class UnifiedView:
    """The output shape required by §7: shared cash + per-provider balances.

    Providers are kept as a separate list of dicts — never merged into a
    single numeric field — so cross-provider leakage tests can prove the data
    structure preserves separation even before the API is hit.
    """

    cash_on_hand: float
    providers: List[dict]   # [{"name": "bkash", "balance": ..., "opening": ..., "confidence": ...}]
    aggregate_confidence: float  # 0.0-1.0
    notes: List[str]


def aggregate_view(
    *,
    cash_on_hand: float,
    feeds: Sequence[SyntheticProviderFeed],
    per_provider_confidence: Optional[Sequence[float]] = None,
) -> UnifiedView:
    if not feeds:
        raise ValueError("at least one provider feed required")
    confidences = list(per_provider_confidence) if per_provider_confidence else [1.0] * len(feeds)
    if len(confidences) != len(feeds):
        raise ValueError("confidence list length must match feed count")
    providers = []
    for feed, conf in zip(feeds, confidences):
        providers.append(
            {
                "name": feed.provider,
                "opening_balance": feed.opening_balance,
                "current_balance": feed.current_balance,
                "confidence": round(float(conf), 3),
            }
        )
    aggregate_confidence = round(statistics.fmean(confidences), 3)
    notes: List[str] = []
    return UnifiedView(
        cash_on_hand=cash_on_hand,
        providers=providers,
        aggregate_confidence=aggregate_confidence,
        notes=notes,
    )


@dataclass
class ShortageForecast:
    provider: str
    hours_to_zero: float
    confidence: float
    confidence_band_hours: float  # +/- on hours_to_zero
    model: str


def forecast_shortage(
    feed: SyntheticProviderFeed,
    *,
    horizon_minutes: int = 240,
    sample_window_min: int = 30,
) -> ShortageForecast:
    """Linear regression over the last ``sample_window_min`` minutes of activity.

    Returns ``hours_to_zero`` extrapolated from observed drain rate. If the
    feed is empty, returns a very large hours-to-zero with confidence 0.
    """
    if not feed.transactions:
        return ShortageForecast(
            provider=feed.provider,
            hours_to_zero=1e9,
            confidence=0.0,
            confidence_band_hours=1e9,
            model="empty_feed",
        )

    amounts = [t["amount"] for t in feed.transactions[:sample_window_min]]
    if len(amounts) < 3:
        # Not enough data -> very wide band, low confidence.
        avg = statistics.fmean(amounts) if amounts else 0.0
        rate_per_min = avg / max(sample_window_min, 1)
        return ShortageForecast(
            provider=feed.provider,
            hours_to_zero=(feed.current_balance / rate_per_min / 60) if rate_per_min > 0 else 1e9,
            confidence=0.2,
            confidence_band_hours=24.0,
            model="insufficient_data",
        )

    n = len(amounts)
    xs = list(range(n))
    ys = amounts
    x_mean = statistics.fmean(xs)
    y_mean = statistics.fmean(ys)
    num = sum((x - x_mean) * (y - y_mean) for x, y in zip(xs, ys))
    den = sum((x - x_mean) ** 2 for x in xs) or 1e-9
    slope = num / den  # BDT per minute
    intercept = y_mean - slope * x_mean
    if slope <= 0:
        return ShortageForecast(
            provider=feed.provider,
            hours_to_zero=1e9,
            confidence=0.4,
            confidence_band_hours=48.0,
            model="non_draining",
        )

    minutes_to_zero = feed.current_balance / slope
    hours_to_zero = minutes_to_zero / 60.0

    # Confidence scales with sample size (logistic) and decreases if the
    # residual variance is large relative to the signal.
    residuals = [abs((slope * x + intercept) - y) for x, y in zip(xs, ys)]
    sigma = statistics.pstdev(residuals) if len(residuals) > 1 else 0.0
    signal_to_noise = y_mean / (sigma or 1e-9)
    confidence = max(0.1, min(0.95, 1 / (1 + math.exp(-(n - 5) / 5)) * min(signal_to_noise / 3, 1.0)))
    band_minutes = (60.0 / slope) * (sigma or 1.0) * 1.96  # 95% interval, in minutes
    return ShortageForecast(
        provider=feed.provider,
        hours_to_zero=round(hours_to_zero, 2),
        confidence=round(confidence, 3),
        confidence_band_hours=round(band_minutes / 60.0, 2),
        model="linear_regression_windowed",
    )


def minutes_until_actual_zero(feed: SyntheticProviderFeed) -> float:
    """For lead-time measurement — the wall-clock minute when balance hits zero
    on the synthetic feed.
    """
    bal = feed.opening_balance
    for i, t in enumerate(feed.transactions, start=1):
        bal -= t["amount"]
        if bal <= 0:
            return float(i)
    return float("inf")


def compute_unified_view(*args, **kwargs) -> UnifiedView:
    """Compatibility alias for tests that import ``compute_unified_view``."""
    return aggregate_view(*args, **kwargs)


def detect_simultaneous_shortages(
    feeds: Iterable[SyntheticProviderFeed],
    *,
    threshold_hours: float = 2.0,
    horizon_minutes: int = 240,
) -> List[ShortageForecast]:
    out: List[ShortageForecast] = []
    for feed in feeds:
        f = forecast_shortage(feed, horizon_minutes=horizon_minutes)
        if f.hours_to_zero <= threshold_hours:
            out.append(f)
    return out