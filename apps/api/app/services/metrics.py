"""Module 9 — Metrics & Model Validation.

Computes the seven required + one optional metric against the simulated ground
truth logged by the data simulation engine. Numbers are persisted in `metric_ticks`
so the dashboard can render a live time series, not a single snapshot.
"""
from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta
from statistics import mean
from typing import Dict, List, Optional

from sqlmodel import Session, select

from ..models.database import (
    Alert,
    AnomalyEvent,
    ForecastSnapshot,
    MetricTick,
    ScenarioEvent,
    Transaction,
)


@dataclass
class MetricsSnapshot:
    liquidity_mae_minutes: float
    shortage_lead_time_minutes: float
    anomaly_precision: float
    anomaly_recall: float
    false_positive_rate: float
    explanation_coverage: float
    api_latency_p50_ms: float
    api_latency_p95_ms: float
    confidence_delta_under_bad_data: float
    priority_classification_alignment: Optional[float]
    alert_count: int
    anomaly_event_count: int
    generated_at: datetime


# ---------------------------------------------------------------------------
# Anomaly precision / recall
# ---------------------------------------------------------------------------

def _pr_metrics(scenario_events: List[ScenarioEvent], anomaly_events: List[AnomalyEvent]) -> tuple:
    truth_pos = {s.id for s in scenario_events if s.is_anomaly_ground_truth}
    detected_agencies: List[int] = []
    for a in anomaly_events:
        # Match any anomaly event to the most-recent ground-truth scenario of the same provider
        for s in scenario_events[::-1]:
            if s.provider == a.provider:
                detected_agencies.append(s.id)
                break

    tp = sum(1 for s in detected_agencies if s in truth_pos)
    fp = len(detected_agencies) - tp
    fn = len(truth_pos) - tp

    # Tolerate trivial division-by-zero
    precision = tp / (tp + fp) if (tp + fp) > 0 else (1.0 if not truth_pos else 0.0)
    recall = tp / (tp + fn) if (tp + fn) > 0 else 1.0

    total_alerts = tp + fp
    fpr = fp / total_alerts if total_alerts > 0 else 0.0
    return precision, recall, fpr


# ---------------------------------------------------------------------------
# Liquidity MAE + lead time
# ---------------------------------------------------------------------------

def _liquidity_mae_and_lead(session: Session, scenarios: List[ScenarioEvent]) -> tuple:
    """For each liquidity-pressure scenario, look up the first forecast that
    predicted a shortage time, and compare to the moment the provider balance
    actually reached <=10% of its baseline (proxy for 'actual depletion')."""
    from ..models.database import BalanceHistory

    diffs: List[float] = []
    leads: List[float] = []
    for s in scenarios:
        if s.kind not in ("bkash_surge",):
            continue
        first_forecast = session.exec(
            select(ForecastSnapshot).where(ForecastSnapshot.agent_id == s.agent_id)
            .where(ForecastSnapshot.provider == s.provider)
            .where(ForecastSnapshot.hours_to_shortage != None)  # noqa: E711
            .where(ForecastSnapshot.ts >= s.injected_at)
            .order_by(ForecastSnapshot.ts)
        ).first()
        if first_forecast is None or first_forecast.hours_to_shortage is None:
            continue
        baseline = {"bkash": 5_000.0, "nagad": 80_000.0, "rocket": 40_000.0}[s.provider]
        threshold = 0.1 * baseline
        actual = session.exec(
            select(BalanceHistory)
            .where(BalanceHistory.agent_id == s.agent_id)
            .where(BalanceHistory.provider == s.provider)
            .where(BalanceHistory.balance <= threshold)
            .where(BalanceHistory.ts >= s.injected_at)
            .order_by(BalanceHistory.ts)
        ).first()
        if actual is None:
            continue
        predicted_min = first_forecast.hours_to_shortage * 60.0
        actual_min = (actual.ts - first_forecast.ts).total_seconds() / 60.0
        diffs.append(abs(predicted_min - actual_min))
        leads.append(predicted_min)  # lead time = how early we sounded the alarm
    mae = mean(diffs) if diffs else 0.0
    lead = mean(leads) if leads else 0.0
    return mae, lead


# ---------------------------------------------------------------------------
# Confidence under bad data (Module 8)
# ---------------------------------------------------------------------------

def _confidence_delta(session: Session) -> float:
    healthy = session.exec(
        select(ForecastSnapshot.confidence).where(ForecastSnapshot.data_quality >= 0.95)
    ).all()
    degraded = session.exec(
        select(ForecastSnapshot.confidence).where(ForecastSnapshot.data_quality < 0.7)
    ).all()
    if not healthy or not degraded:
        return 0.0
    return mean(healthy) - mean(degraded)


# ---------------------------------------------------------------------------
# Priority classification alignment (optional 8th metric)
# ---------------------------------------------------------------------------

def _priority_alignment(scenarios: List[ScenarioEvent], alerts: List[Alert]) -> Optional[float]:
    by_intent = {s.kind: s.intended_severity for s in scenarios}
    matched = 0
    total = 0
    for kind, intent in by_intent.items():
        if intent == "normal":
            continue  # 'normal' doesn't get a label match; skip
        total += 1
        alerts_for_kind = [a for a in alerts if a.ground_truth_severity == intent]
        if alerts_for_kind:
            # Did *any* alert from this scenario end up at the matching tier?
            top = max(alerts_for_kind, key=lambda a: a.priority_score)
            tier_match = {"normal": "normal", "low": "low", "high": "high", "critical": "critical"}.get(intent)
            if top.severity == tier_match:
                matched += 1
        else:
            # No alert raised — that's a miss too. Treat as 0 for the metric.
            pass
    if total == 0:
        return None
    return matched / total


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def compute_metrics(session: Session) -> MetricsSnapshot:
    now = datetime.utcnow()
    scenarios = session.exec(select(ScenarioEvent)).all()
    anomaly_events = session.exec(select(AnomalyEvent)).all()
    alerts = session.exec(select(Alert)).all()
    latency_rows = session.exec(select(MetricTick).where(MetricTick.name == "api_latency_ms").order_by(MetricTick.ts.desc()).limit(200)).all()
    latencies = [r.value for r in latency_rows]

    if not anomaly_events and not scenarios:
        # Touch the engine so we have something to report even at cold start
        fallback_precision, fallback_recall, fallback_fpr = 1.0, 1.0, 0.0
    else:
        fallback_precision, fallback_recall, fallback_fpr = _pr_metrics(scenarios, anomaly_events)

    mae, lead = _liquidity_mae_and_lead(session, scenarios)
    conf_delta = _confidence_delta(session)
    priority_align = _priority_alignment(scenarios, alerts)

    # Explanation coverage: alerts that have non-empty reasons + confidence
    if alerts:
        covered = sum(1 for a in alerts if a.reasons_json and a.reasons_json != "[]" and a.confidence > 0)
        coverage = covered / len(alerts)
    else:
        coverage = 1.0

    p50 = _percentile(latencies, 50)
    p95 = _percentile(latencies, 95)

    snap = MetricsSnapshot(
        liquidity_mae_minutes=float(mae),
        shortage_lead_time_minutes=float(lead),
        anomaly_precision=float(fallback_precision),
        anomaly_recall=float(fallback_recall),
        false_positive_rate=float(fallback_fpr),
        explanation_coverage=float(coverage),
        api_latency_p50_ms=float(p50),
        api_latency_p95_ms=float(p95),
        confidence_delta_under_bad_data=float(conf_delta),
        priority_classification_alignment=priority_align if priority_align is None else float(priority_align),
        alert_count=len(alerts),
        anomaly_event_count=len(anomaly_events),
        generated_at=now,
    )

    # Persist most-recent values for time-series plots
    for name, val in (
        ("liquidity_mae_minutes", snap.liquidity_mae_minutes),
        ("shortage_lead_time_minutes", snap.shortage_lead_time_minutes),
        ("anomaly_precision", snap.anomaly_precision),
        ("anomaly_recall", snap.anomaly_recall),
        ("false_positive_rate", snap.false_positive_rate),
        ("explanation_coverage", snap.explanation_coverage),
    ):
        session.add(MetricTick(name=name, value=val))
    session.commit()
    return snap


def _percentile(values: List[float], pct: float) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    idx = int(round((pct / 100.0) * (len(s) - 1)))
    return float(s[max(0, min(idx, len(s) - 1))])


def record_api_latency(session: Session, ms: float) -> None:
    session.add(MetricTick(name="api_latency_ms", value=float(ms)))
    session.commit()
