"""Environment / config loader for the test suite.

Loads ``.env.test`` (or any ``*.test.env``) once at import time and exposes a
``Config`` object with sane defaults so the suite is runnable locally before
the prototype is wired up. The API token is read from ``API_TOKEN`` and is
never logged or printed.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

_REPO_ROOT = Path(__file__).resolve().parents[2]
_ENV_FILES = (_REPO_ROOT / ".env.test", _REPO_ROOT / ".env.test.local")


def _load_env_files() -> None:
    """Tiny ``.env`` loader so we don't depend on python-dotenv being installed."""
    for env_file in _ENV_FILES:
        if not env_file.exists():
            continue
        try:
            for raw in env_file.read_text(encoding="utf-8").splitlines():
                line = raw.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                k = k.strip()
                v = v.strip().strip('"').strip("'")
                # Never overwrite a value that's already set in the shell.
                os.environ.setdefault(k, v)
        except OSError as exc:  # pragma: no cover - filesystem guard
            log.warning("Could not read %s: %s", env_file, exc)


_load_env_files()


@dataclass(frozen=True)
class Config:
    api_base_url: str
    api_token: Optional[str]
    agent_id_header: str
    timeout_s: float
    reports_dir: Path
    short_term_horizon_minutes: int
    demand_error_band_pct: float
    fpr_threshold: float
    lead_time_target_hours: float

    @classmethod
    def load(cls) -> "Config":
        api_base = os.environ.get("API_BASE_URL", "http://localhost:8000/api/").rstrip("/") + "/"
        token = os.environ.get("API_TOKEN")
        if not token:
            log.warning(
                "API_TOKEN is not set. Authenticated tests will be skipped. "
                "Set API_TOKEN in .env.test before running the suite."
            )
        reports = _REPO_ROOT / "tests" / "reports"
        reports.mkdir(parents=True, exist_ok=True)
        return cls(
            api_base_url=api_base,
            api_token=token,
            agent_id_header=os.environ.get("AGENT_ID_HEADER", "X-Agent-Id"),
            timeout_s=float(os.environ.get("HTTP_TIMEOUT", "10")),
            reports_dir=reports,
            short_term_horizon_minutes=int(os.environ.get("FORECAST_HORIZON_MIN", "240")),
            demand_error_band_pct=float(os.environ.get("DEMAND_ERROR_BAND_PCT", "15")),
            fpr_threshold=float(os.environ.get("FPR_THRESHOLD", "0.05")),
            lead_time_target_hours=float(os.environ.get("LEAD_TIME_TARGET_HOURS", "2")),
        )


CONFIG = Config.load()


def mask_token(tok: Optional[str]) -> str:
    """Return a masked representation of a token for safe logging."""
    if not tok:
        return "<unset>"
    if len(tok) <= 6:
        return "***"
    return f"{tok[:2]}***{tok[-2:]}"