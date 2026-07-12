"""Module 9 — Metrics & Model Validation.

Computes the seven required + one optional metric against the simulated ground
truth logged by the data simulation engine. Numbers are persisted in `metric_ticks`
so the dashboard can render a live time series, not a single snapshot.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from statistics import mean
from typing import List, Optional

from sqlmodel import Session, select

from ..models.database import (
    Alert,
    AnomalyEvent,
    ForecastSnapshot,
    MetricTick,
    ScenarioEvent,
)


@dataclass
class MetricsSnapshot:
    liquidity_mae_minutes: Optional[float]
    shortage_lead_time_minutes: Optional[float]
    anomaly_precision: Optional[float]
    anomaly_recall: Optional[float]
    false_positive_rate: Optional[float]
    explanation_coverage: Optional[float]
    api_latency_p50_ms: Optional[float]
    api_latency_p95_ms: Optional[float]
    confidence_delta_under_bad_data: Optional[float]
    priority_classification_alignment: Optional[float]
    alert_count: int
    anomaly_event_count: int
    generated_at: datetime


# ---------------------------------------------------------------------------
# Anomaly precision / recall
# ---------------------------------------------------------------------------

def _pr_metrics(scenario_events: List[ScenarioEvent], anomaly_events: List[AnomalyEvent]) -> tuple:
    # Score at scenario level (not event level): several rule heads may fire for
    # one injected case and must not be counted as several independent guesses.
    tp = fp = fn = normal_total = 0
    for scenario in scenario_events:
        duration = max(1, int(getattr(scenario, "duration_minutes", 5) or 5))
        end = scenario.injected_at + timedelta(minutes=duration)
        detected = any(
            event.agent_id == scenario.agent_id
            and event.provider == scenario.provider
            and scenario.injected_at <= event.ts <= end
            for event in anomaly_events
        )
        if scenario.is_anomaly_ground_truth:
            if detected:
                tp += 1
            else:
                fn += 1
        else:
            normal_total += 1
            if detected:
                fp += 1

    # Undefined metrics stay undefined. Reporting perfect precision/recall at
    # cold start would be a confident claim without evaluated examples.
    precision = tp / (tp + fp) if (tp + fp) > 0 else None
    recall = tp / (tp + fn) if (tp + fn) > 0 else None
    fpr = fp / normal_total if normal_total > 0 else None
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
            .limit(1)
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
            .limit(1)
        ).first()
        if actual is None:
            continue
        predicted_min = first_forecast.hours_to_shortage * 60.0
        actual_min = (actual.ts - first_forecast.ts).total_seconds() / 60.0
        diffs.append(abs(predicted_min - actual_min))
        # Detection lead time is the wall-clock warning interval, independent
        # of whether the ETA itself was accurate.
        leads.append(actual_min)
    mae = mean(diffs) if diffs else None
    lead = mean(leads) if leads else None
    return mae, lead


# ---------------------------------------------------------------------------
# Confidence under bad data (Module 8)
# ---------------------------------------------------------------------------

def _confidence_delta(session: Session) -> Optional[float]:
    # Cap each side at a window-bounded sample so the metric stays
    # responsive as the forecast table grows.
    window_start = datetime.utcnow() - timedelta(hours=24)
    healthy = session.exec(
        select(ForecastSnapshot.confidence)
        .where(ForecastSnapshot.data_quality >= 0.95)
        .where(ForecastSnapshot.ts >= window_start)
        .order_by(ForecastSnapshot.ts.desc())
        .limit(500)
    ).all()
    degraded = session.exec(
        select(ForecastSnapshot.confidence)
        .where(ForecastSnapshot.data_quality < 0.7)
        .where(ForecastSnapshot.ts >= window_start)
        .order_by(ForecastSnapshot.ts.desc())
        .limit(500)
    ).all()
    if not healthy or not degraded:
        return None
    return mean(healthy) - mean(degraded)


# ---------------------------------------------------------------------------
# Priority classification alignment (optional 8th metric)
# ---------------------------------------------------------------------------

def _priority_alignment(scenarios: List[ScenarioEvent], alerts: List[Alert]) -> Optional[float]:
    matched = 0
    total = 0
    for scenario in scenarios:
        intent = scenario.intended_severity
        if intent == "normal":
            continue  # 'normal' doesn't get a label match; skip
        total += 1
        duration = max(1, int(getattr(scenario, "duration_minutes", 5) or 5))
        end = scenario.injected_at + timedelta(minutes=duration + 1)
        candidates = [
            alert
            for alert in alerts
            if alert.agent_id == scenario.agent_id
            and alert.provider == scenario.provider
            and scenario.injected_at <= alert.updated_at <= end
        ]
        if candidates and max(candidates, key=lambda a: a.priority_score).severity == intent:
            matched += 1
    if total == 0:
        return None
    return matched / total


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def compute_metrics(session: Session) -> MetricsSnapshot:
    now = datetime.utcnow()
    # Bound the rows we materialize: metric semantics are "last 24h of
    # activity". Without this, a long-running demo session causes the
    # /metrics endpoint to scan every row ever written, on every call.
    _METRICS_WINDOW_HOURS = 24
    _METRICS_HARD_CAP = 2000
    window_start = now - timedelta(hours=_METRICS_WINDOW_HOURS)
    scenarios = session.exec(
        select(ScenarioEvent)
        .where(ScenarioEvent.injected_at >= window_start)
        .order_by(ScenarioEvent.injected_at.desc())
        .limit(_METRICS_HARD_CAP)
    ).all()
    anomaly_events = session.exec(
        select(AnomalyEvent)
        .where(AnomalyEvent.ts >= window_start)
        .order_by(AnomalyEvent.ts.desc())
        .limit(_METRICS_HARD_CAP)
    ).all()
    alerts = session.exec(
        select(Alert)
        .where(Alert.created_at >= window_start)
        .order_by(Alert.created_at.desc())
        .limit(_METRICS_HARD_CAP)
    ).all()
    latency_rows = session.exec(select(MetricTick).where(MetricTick.name == "api_latency_ms").order_by(MetricTick.ts.desc()).limit(200)).all()
    latencies = [r.value for r in latency_rows]

    precision, recall, fpr = _pr_metrics(scenarios, anomaly_events)

    mae, lead = _liquidity_mae_and_lead(session, scenarios)
    conf_delta = _confidence_delta(session)
    priority_align = _priority_alignment(scenarios, alerts)

    # Explanation coverage: reason + record/source evidence + uncertainty.
    if alerts:
        covered = sum(
            1 for a in alerts
            if a.reasons_json and a.reasons_json != "[]"
            and a.evidence_json and a.evidence_json != "[]"
            and a.confidence is not None and 0.0 <= a.confidence <= 1.0
        )
        coverage = covered / len(alerts)
    else:
        coverage = None

    p50 = _percentile(latencies, 50)
    p95 = _percentile(latencies, 95)

    snap = MetricsSnapshot(
        liquidity_mae_minutes=None if mae is None else float(mae),
        shortage_lead_time_minutes=None if lead is None else float(lead),
        anomaly_precision=None if precision is None else float(precision),
        anomaly_recall=None if recall is None else float(recall),
        false_positive_rate=None if fpr is None else float(fpr),
        explanation_coverage=None if coverage is None else float(coverage),
        api_latency_p50_ms=None if p50 is None else float(p50),
        api_latency_p95_ms=None if p95 is None else float(p95),
        confidence_delta_under_bad_data=None if conf_delta is None else float(conf_delta),
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
        if val is not None:
            session.add(MetricTick(name=name, value=val))
    session.commit()
    return snap


def _percentile(values: List[float], pct: float) -> Optional[float]:
    if not values:
        return None
    s = sorted(values)
    idx = int(round((pct / 100.0) * (len(s) - 1)))
    return float(s[max(0, min(idx, len(s) - 1))])


def record_api_latency(session: Session, ms: float) -> None:
    session.add(MetricTick(name="api_latency_ms", value=float(ms)))
    session.commit()
