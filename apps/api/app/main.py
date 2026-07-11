"""FastAPI app — entry point. Wires the routers, seeds the DB, and serves CORS
so the Next.js frontend can call from a different port."""
from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlmodel import Session

from .db import engine, init_db
from .routers import alerts, cash_support, config_router, dashboard, metrics, notifications, scenarios, users
from .services.seed import seed_if_empty
from .services.notifications import backfill_open_case_assignments
from .services.cases import normalize_open_case_ownership, process_due_escalations
from .services.explanations import backfill_case_explanations


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    with Session(engine) as s:
        seed_if_empty(s)
        backfill_open_case_assignments(s)
        normalize_open_case_ownership(s)
        backfill_case_explanations(s)
        process_due_escalations(s)
    yield


app = FastAPI(
    title="Super Agent Liquidity & Risk Intelligence Platform",
    version="0.1.0",
    description="Advisory decision-support API — never executes transactions or labels wrongdoing.",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://127.0.0.1:3000", "*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/")
def root():
    return {
        "name": "Super Agent Liquidity & Risk Intelligence Platform",
        "version": app.version,
        "disclaimer": "Simulated environment. Advisory signals require human review; no transactions are executed.",
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
app.include_router(cash_support.router)
app.include_router(notifications.router)
