"""SQLite database setup. Single-file, in-process — fine for the prototype."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Iterator

from sqlmodel import Session, SQLModel, create_engine
from sqlalchemy import inspect, text

import app.models  # noqa: F401  -- ensure models are registered with SQLModel.metadata

_DATA_DIR = Path(os.environ.get("SUPER_AGENT_DATA", "./.data"))
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
    # forecast_snapshots.summary — curated one-line basis for UI / alerts.
    _add_column_if_missing(
        "forecast_snapshots", "summary", 'summary VARCHAR NOT NULL DEFAULT ""'
    )


def init_db() -> None:
    SQLModel.metadata.create_all(engine)
    _run_additive_migrations()


def get_session() -> Iterator[Session]:
    with Session(engine) as session:
        yield session