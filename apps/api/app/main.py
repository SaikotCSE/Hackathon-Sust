"""FastAPI app — entry point. Wires the routers, seeds the DB, and serves CORS
so the Next.js frontend can call from a different port."""
from __future__ import annotations

import json
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from sqlmodel import Session

from .db import engine, init_db
from .models.database import Alert, Case
from .routers import alerts, config_router, dashboard, metrics, scenarios, users
from .services.cases import auto_escalate_due
from .services.seed import seed_if_empty
from .simulation.engine import PROVIDERS, data_quality_for


def _escalation_config() -> dict:
    """Re-read escalation timing from decision-weights.json each tick."""
    try:
        from pathlib import Path
        with (Path(__file__).resolve().parents[3] / "config" / "decision-weights.json").open() as fh:
            return json.load(fh).get("_escalation", {})
    except Exception:
        return {"critical_minutes": 10, "high_minutes": 30}


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    with Session(engine) as s:
        seed_if_empty(s)
    yield


app = FastAPI(
    title="Super Agent Liquidity & Risk Intelligence Platform",
    version="0.1.0",
    description="Decision-support API — never executes real transactions, never claims fraud.",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://127.0.0.1:3000", "*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def escalation_middleware(request: Request, call_next):
    """Cheap background task: before each request, fire any due escalations.
    Real product would put this on a scheduler, not a middleware.

    SAFETY: this middleware performs *routing only* (auto_escalate_due flips
    alert status from `open` to `escalated` after the SLA timer). It does
    not resolve, close, or otherwise decide outcomes on cases — that is
    reserved for an authenticated Risk/Compliance analyst via
    POST /alerts/{id}/transition. Do not extend this middleware to make
    case outcomes.
    """
    if request.url.path.startswith("/alerts") or request.url.path.startswith("/metrics"):
        try:
            with Session(engine) as s:
                # Refresh data-quality state once per request
                dq = {p: data_quality_for(s, p) for p in PROVIDERS}
                # Re-evaluate orchestration periodically (every 5th call to /alerts)
                from .services.orchestrator import run_orchestration_cycle
                if request.url.path.startswith("/alerts") and request.method == "GET":
                    from .models.database import Agent
                    agent = s.exec(__import__("sqlmodel").select(Agent).limit(1)).first()
                    if agent is not None:
                        run_orchestration_cycle(s, agent.id, providers=list(PROVIDERS), data_quality_by_provider=dq)
                auto_escalate_due(s, escalation_cfg=_escalation_config())
        except Exception:
            pass
    return await call_next(request)


@app.get("/")
def root():
    return {
        "name": "Super Agent Liquidity & Risk Intelligence Platform",
        "version": app.version,
        "disclaimer": "Simulated environment. Never executes real transactions. Never claims fraud.",
        "endpoints": [
            "/dashboard", "/alerts", "/alerts/{id}", "/alerts/{id}/transition",
            "/simulation/tick", "/simulation/inject", "/simulation/resolve-data-quality",
            "/metrics/snapshot", "/metrics/series",
            "/config/decision-weights", "/config/reload", "/users",
        ],
    }


app.include_router(dashboard.router)
app.include_router(alerts.router)
app.include_router(scenarios.router)
app.include_router(metrics.router)
app.include_router(config_router.router)
app.include_router(users.router)