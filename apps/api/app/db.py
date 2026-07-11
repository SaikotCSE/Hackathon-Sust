"""SQLite database setup. Single-file, in-process — fine for the prototype."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Iterator

from sqlmodel import Session, SQLModel, create_engine
from sqlalchemy import inspect, text

import app.models  # noqa: F401  -- ensure models are registered with SQLModel.metadata

_PROJECT_ROOT = Path(__file__).resolve().parents[3]
_DATA_DIR = Path(os.environ["SUPER_AGENT_DATA"]) if "SUPER_AGENT_DATA" in os.environ else _PROJECT_ROOT / ".data"
_DATA_DIR.mkdir(parents=True, exist_ok=True)
_DB_PATH = _DATA_DIR / "super_agent.db"
DATABASE_URL = f"sqlite:///{_DB_PATH}"

engine = create_engine(
    DATABASE_URL,
    echo=False,
    connect_args={"check_same_thread": False},
)


# ---------------------------------------------------------------------------
# Lightweight in-place migrations.
#
# SQLModel/SQLAlchemy `create_all()` only creates tables that don't exist yet;
# it does not add new columns to existing tables. When we add a field to a
# model in code, an old SQLite file (or a seeded DB) will keep the old schema
# and start failing with `no such column: <table>.<column>`. For our prototype
# we patch that here with cheap additive ALTER TABLEs guarded by an
# introspection check. Replace with Alembic before any production rollout.
# ---------------------------------------------------------------------------
def _add_column_if_missing(table: str, column: str, ddl: str) -> None:
    """Add ``column`` to ``table`` if it isn't already present."""
    insp = inspect(engine)
    if table not in insp.get_table_names():
        return
    if column in {c["name"] for c in insp.get_columns(table)}:
        return
    with engine.begin() as conn:
        conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {ddl}"))


def _run_additive_migrations() -> None:
    for column, ddl in (
        ("contact_name", 'VARCHAR NOT NULL DEFAULT "Outlet contact"'),
        ("contact_phone", 'VARCHAR NOT NULL DEFAULT "+8801700000000"'),
        ("field_officer_name", 'VARCHAR NOT NULL DEFAULT "Assigned Field Officer"'),
        ("field_officer_phone", 'VARCHAR NOT NULL DEFAULT "+8801800000000"'),
        ("area_manager_name", 'VARCHAR NOT NULL DEFAULT "Area Operations Manager"'),
        ("area_manager_phone", 'VARCHAR NOT NULL DEFAULT "+8801900000000"'),
    ):
        _add_column_if_missing("agents", column, f"{column} {ddl}")
    # forecast_snapshots.summary — curated one-line basis for UI / alerts.
    _add_column_if_missing(
        "forecast_snapshots", "summary", 'summary VARCHAR NOT NULL DEFAULT ""'
    )
    _add_column_if_missing(
        "forecast_snapshots", "burn_rate_per_min", "burn_rate_per_min FLOAT NOT NULL DEFAULT 0"
    )
    _add_column_if_missing(
        "scenario_events", "duration_minutes", "duration_minutes INTEGER NOT NULL DEFAULT 5"
    )
    for column, ddl in (
        ("forecast_balance", "FLOAT"),
        ("forecast_burn_rate_per_min", "FLOAT"),
        ("coverage_hours", "FLOAT NOT NULL DEFAULT 8"),
        ("target_balance", "FLOAT"),
        ("calculation", 'VARCHAR NOT NULL DEFAULT ""'),
        ("applied_amount", "FLOAT NOT NULL DEFAULT 0"),
        ("balance_after", "FLOAT"),
        ("applied_at", "DATETIME"),
    ):
        _add_column_if_missing("cash_support_requests", column, f"{column} {ddl}")
    for column, ddl in (
        ("explanation_json", 'VARCHAR NOT NULL DEFAULT "{}"'),
        ("explanation_provider", 'VARCHAR NOT NULL DEFAULT "fallback"'),
        ("explanation_model", 'VARCHAR NOT NULL DEFAULT "deterministic-evidence-v1"'),
        ("explanation_status", 'VARCHAR NOT NULL DEFAULT "pending"'),
        ("explanation_error", "VARCHAR"),
        ("explanation_generated_at", "DATETIME"),
        ("explanation_language", 'VARCHAR NOT NULL DEFAULT "en"'),
    ):
        _add_column_if_missing("cases", column, f"{column} {ddl}")
    for column, ddl in (
        ("risk_recommendation", "VARCHAR"),
        ("risk_recommendation_note", "VARCHAR"),
        ("risk_recommended_by", "VARCHAR"),
        ("risk_recommended_at", "DATETIME"),
    ):
        _add_column_if_missing("cases", column, f"{column} {ddl}")
    for column, ddl in (
        ("assigned_to", 'VARCHAR NOT NULL DEFAULT "Operations queue"'),
        ("assigned_contact_type", 'VARCHAR NOT NULL DEFAULT "operations"'),
        ("resolution_code", "VARCHAR"),
        ("resolution_summary", "VARCHAR"),
        ("closed_at", "DATETIME"),
    ):
        _add_column_if_missing("cases", column, f"{column} {ddl}")


def init_db() -> None:
    SQLModel.metadata.create_all(engine)
    _run_additive_migrations()


def get_session() -> Iterator[Session]:
    with Session(engine) as session:
        yield session
