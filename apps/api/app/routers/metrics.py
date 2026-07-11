"""Metrics endpoints — Module 9."""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import List

from fastapi import APIRouter, Depends
from sqlmodel import Session, select

from ..db import get_session
from ..models.database import MetricTick
from ..services.auth import Principal, current_principal
from ..services.metrics import compute_metrics


router = APIRouter(tags=["metrics"])


@router.get("/metrics/snapshot")
def metrics_snapshot(
    principal: Principal = Depends(current_principal),
    session: Session = Depends(get_session),
):
    snap = compute_metrics(session)
    return {
        "liquidity_mae_minutes": snap.liquidity_mae_minutes,
        "shortage_lead_time_minutes": snap.shortage_lead_time_minutes,
        "anomaly_precision": snap.anomaly_precision,
        "anomaly_recall": snap.anomaly_recall,
        "false_positive_rate": snap.false_positive_rate,
        "explanation_coverage": snap.explanation_coverage,
        "api_latency_p50_ms": snap.api_latency_p50_ms,
        "api_latency_p95_ms": snap.api_latency_p95_ms,
        "confidence_delta_under_bad_data": snap.confidence_delta_under_bad_data,
        "priority_classification_alignment": snap.priority_classification_alignment,
        "alert_count": snap.alert_count,
        "anomaly_event_count": snap.anomaly_event_count,
        "generated_at": snap.generated_at.isoformat(),
    }


@router.get("/metrics/series")
def metrics_series(
    name: str,
    minutes: int = 60,
    principal: Principal = Depends(current_principal),
    session: Session = Depends(get_session),
):
    since = datetime.utcnow() - timedelta(minutes=minutes)
    rows = session.exec(
        select(MetricTick).where(MetricTick.name == name).where(MetricTick.ts >= since).order_by(MetricTick.ts)
    ).all()
    return {"name": name, "points": [{"ts": r.ts.isoformat(), "value": r.value} for r in rows]}