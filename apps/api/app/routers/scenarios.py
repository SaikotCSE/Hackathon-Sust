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
from ..services.cases import process_due_escalations
from ..services.seed import seed_if_empty
from ..simulation.engine import PROVIDERS, ScenarioSpec, SimulationEngine, data_quality_for, resolve_open_data_quality


router = APIRouter(tags=["simulation"])


@router.post("/simulation/tick")
def tick(
    n_transactions: int = 8,
    principal: Principal = Depends(current_principal),
    session: Session = Depends(get_session),
):
    if principal.role not in ("agent", "ops"):
        raise HTTPException(403, "only agents and Operations may advance the simulated outlet")
    agent = seed_if_empty(session)
    if principal.role == "agent" and principal.agent_id != agent.id:
        raise HTTPException(403, "agents may advance only their own simulated outlet")
    started = time.time()
    engine = SimulationEngine(session, agent.id)
    engine.tick(n_transactions=n_transactions)

    # Compute data-quality scores per provider (Module 8)
    dq = {p: data_quality_for(session, p) for p in PROVIDERS}

    # Run Module 2 + 3 + 4 → produce alerts (orchestration cycle)
    new_alerts = run_orchestration_cycle(
        session, agent.id, providers=list(PROVIDERS), data_quality_by_provider=dq,
    )
    escalated_cases = process_due_escalations(session)
    # Physical cash has no provider anomaly stream, but it must receive a fresh
    # forecast on every tick just like the e-money wallets.
    from ..services.liquidity import compute_forecast
    compute_forecast(session, agent.id, "physical", data_quality=1.0)

    elapsed_ms = (time.time() - started) * 1000.0
    record_api_latency(session, elapsed_ms)
    return {
        "ticked": n_transactions,
        "new_alerts": [{"id": a.id, "severity": a.severity, "title": a.title, "provider": a.provider}
                       for a in new_alerts],
        "escalated_cases": escalated_cases,
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
    if principal.role not in ("agent", "ops"):
        raise HTTPException(403, "only agents and Operations can inject prototype scenarios")
    agent = seed_if_empty(session)
    if principal.role == "agent" and principal.agent_id != agent.id:
        raise HTTPException(403, "agents may inject scenarios only for their own outlet")
    kind = payload.get("kind")
    label = payload.get("label", kind)
    provider = payload.get("provider")
    intended = payload.get("intended_severity", "normal")
    is_anomaly = bool(payload.get("is_anomaly", False))
    try:
        duration = int(payload.get("duration_minutes", 8))
    except (TypeError, ValueError):
        raise HTTPException(400, "duration_minutes must be an integer")

    if kind not in ("bkash_surge", "repeated_amount", "structuring", "rocket_delay", "salary_day"):
        raise HTTPException(400, f"unknown scenario kind: {kind}")
    if provider is not None and provider not in PROVIDERS:
        raise HTTPException(400, "provider must be bkash, nagad, or rocket")
    if kind == "bkash_surge" and provider not in (None, "bkash"):
        raise HTTPException(400, "bkash_surge must target bkash")
    if kind == "rocket_delay" and provider not in (None, "rocket"):
        raise HTTPException(400, "rocket_delay must target rocket")
    if not 1 <= duration <= 60:
        raise HTTPException(400, "duration_minutes must be between 1 and 60")
    if intended not in ("normal", "low", "high", "critical"):
        raise HTTPException(400, "intended_severity must be normal, low, high, or critical")
    if not isinstance(label, str) or not label.strip() or len(label) > 160:
        raise HTTPException(400, "label must be a non-empty string of at most 160 characters")

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
    return {
        "scenario_event_id": ev.id,
        "kind": ev.kind,
        "provider": ev.provider,
        "intended_severity": ev.intended_severity,
        "duration_minutes": ev.duration_minutes,
        "analysis_required": True,
    }


@router.post("/simulation/resolve-data-quality")
def resolve_dq(
    payload: dict,
    principal: Principal = Depends(current_principal),
    session: Session = Depends(get_session),
):
    provider = payload.get("provider", "rocket")
    if principal.role == "provider" and principal.provider != provider:
        raise HTTPException(403, "provider wall — not your provider feed")
    if principal.role not in ("provider", "ops"):
        raise HTTPException(403, "only the feed owner or Operations may resolve a feed issue")
    n = resolve_open_data_quality(session, provider)
    return {"resolved": n, "provider": provider}


@router.get("/simulation/scenarios")
def list_scenarios(
    principal: Principal = Depends(current_principal),
    session: Session = Depends(get_session),
):
    q = select(ScenarioEvent)
    if principal.role == "provider":
        q = q.where(ScenarioEvent.provider == principal.provider)
    elif principal.role == "agent":
        q = q.where(ScenarioEvent.agent_id == principal.agent_id)
    rows = session.exec(q.order_by(ScenarioEvent.injected_at.desc()).limit(50)).all()
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
    q = select(DataQualityEvent)
    if principal.role == "provider":
        q = q.where(DataQualityEvent.provider == principal.provider)
    rows = session.exec(q.order_by(DataQualityEvent.started_at.desc()).limit(20)).all()
    return {"events": [
        {
            "id": r.id, "provider": r.provider, "issue": r.issue, "note": r.note,
            "started_at": r.started_at.isoformat(),
            "resolved_at": r.resolved_at.isoformat() if r.resolved_at else None,
        }
        for r in rows
    ]}
