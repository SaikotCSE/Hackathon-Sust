"""Simulation / scenario control endpoints — Module 5 (data sim) + Module 8
(data quality) + the debug panel the demo uses to inject what-if scenarios."""
from __future__ import annotations

import time
from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session, select

from ..db import get_session
from ..models.database import (
    Alert,
    AnomalyEvent,
    BalanceHistory,
    DataQualityEvent,
    ForecastSnapshot,
    ProviderBalance,
    ScenarioEvent,
    Transaction,
)
from ..services.auth import Principal, current_principal
from ..services.metrics import record_api_latency
from ..services.orchestrator import run_orchestration_cycle
from ..services.seed import seed_if_empty
from ..simulation.engine import PROVIDERS, ScenarioSpec, SimulationEngine, data_quality_for, resolve_open_data_quality


router = APIRouter(tags=["simulation"])


@router.post("/simulation/tick")
def tick(
    n_transactions: int = 8,
    principal: Principal = Depends(current_principal),
    session: Session = Depends(get_session),
):
    agent = seed_if_empty(session)
    started = time.time()
    engine = SimulationEngine(session, agent.id)
    engine.tick(n_transactions=n_transactions)

    # Compute data-quality scores per provider (Module 8)
    dq = {p: data_quality_for(session, p) for p in PROVIDERS}

    # Run Module 2 + 3 + 4 → produce alerts (orchestration cycle)
    new_alerts = run_orchestration_cycle(
        session, agent.id, providers=list(PROVIDERS), data_quality_by_provider=dq,
    )

    elapsed_ms = (time.time() - started) * 1000.0
    record_api_latency(session, elapsed_ms)
    return {
        "ticked": n_transactions,
        "new_alerts": [{"id": a.id, "severity": a.severity, "title": a.title, "provider": a.provider}
                       for a in new_alerts],
        "data_quality": dq,
        "latency_ms": elapsed_ms,
    }


@router.post("/simulation/inject")
def inject(
    payload: dict,
    principal: Principal = Depends(current_principal),
    session: Session = Depends(get_session),
):
    """Inject a what-if scenario — the demo debug panel calls this."""
    if principal.role not in ("agent", "ops", "management"):
        raise HTTPException(403, "only agent / ops / management can inject scenarios in the prototype")
    agent = seed_if_empty(session)
    kind = payload.get("kind")
    label = payload.get("label", kind)
    provider = payload.get("provider")
    intended = payload.get("intended_severity", "normal")
    is_anomaly = bool(payload.get("is_anomaly", False))
    duration = int(payload.get("duration_minutes", 8))

    if kind not in ("bkash_surge", "repeated_amount", "structuring", "rocket_delay", "salary_day"):
        raise HTTPException(400, f"unknown scenario kind: {kind}")

    # Default labels per scenario for the ground-truth log
    default_intent = {
        "bkash_surge": "critical",
        "repeated_amount": "high",
        "structuring": "high",
        "rocket_delay": "low",
        "salary_day": "normal",
    }
    default_is_anomaly = {
        "bkash_surge": False,
        "repeated_amount": True,
        "structuring": True,
        "rocket_delay": False,
        "salary_day": False,
    }
    intended = intended if intended != "normal" or kind in ("salary_day",) else default_intent.get(kind, intended)
    is_anomaly = is_anomaly or default_is_anomaly.get(kind, False)

    engine = SimulationEngine(session, agent.id)
    spec = ScenarioSpec(
        kind=kind, label=label, provider=provider,
        intended_severity=intended, is_anomaly_ground_truth=is_anomaly,
        duration_minutes=duration, note=label,
    )
    ev = engine.inject_scenario(spec)
    return {"scenario_event_id": ev.id, "kind": ev.kind, "intended_severity": ev.intended_severity}


@router.post("/simulation/resolve-data-quality")
def resolve_dq(
    payload: dict,
    principal: Principal = Depends(current_principal),
    session: Session = Depends(get_session),
):
    provider = payload.get("provider", "rocket")
    n = resolve_open_data_quality(session, provider)
    return {"resolved": n, "provider": provider}


@router.get("/simulation/scenarios")
def list_scenarios(
    principal: Principal = Depends(current_principal),
    session: Session = Depends(get_session),
):
    rows = session.exec(select(ScenarioEvent).order_by(ScenarioEvent.injected_at.desc()).limit(50)).all()
    return {"scenarios": [
        {
            "id": r.id, "agent_id": r.agent_id, "provider": r.provider,
            "kind": r.kind, "intended_severity": r.intended_severity,
            "is_anomaly_ground_truth": r.is_anomaly_ground_truth,
            "note": r.note, "injected_at": r.injected_at.isoformat(),
        }
        for r in rows
    ]}


@router.get("/simulation/data-quality")
def get_data_quality(
    principal: Principal = Depends(current_principal),
    session: Session = Depends(get_session),
):
    rows = session.exec(select(DataQualityEvent).order_by(DataQualityEvent.started_at.desc()).limit(20)).all()
    return {"events": [
        {
            "id": r.id, "provider": r.provider, "issue": r.issue, "note": r.note,
            "started_at": r.started_at.isoformat(),
            "resolved_at": r.resolved_at.isoformat() if r.resolved_at else None,
        }
        for r in rows
    ]}