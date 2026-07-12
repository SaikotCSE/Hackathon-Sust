"""Pytest configuration shared by every layer.

Provides:
* an ``api_client`` fixture (lazy; tests skip if the API isn't reachable)
* a ``metrics`` fixture backed by the module-level ``MetricsSink``
* a ``detector`` fixture
* a final ``sessionfinish`` hook that writes ``metrics_report.json`` and
  ``judge_scorecard.md`` to ``tests/reports/`` so the artifact is always
  regenerated after a run.
"""
from __future__ import annotations

import logging
import socket
import sys
from pathlib import Path
from typing import Optional

import pytest

# Make the ``tests`` package importable when pytest is invoked from the
# repo root without ``pip install -e .``.
_REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO_ROOT))

from tests.helpers.anomaly import Detector  # noqa: E402
from tests.helpers.api_client import APIClient  # noqa: E402
from tests.helpers.env import CONFIG, mask_token  # noqa: E402
from tests.helpers.metrics import SINK  # noqa: E402
from tests.helpers import synth  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s :: %(message)s",
)
log = logging.getLogger("tests")


def _host_port(url: str) -> tuple[str, int]:
    from urllib.parse import urlparse

    p = urlparse(url)
    host = p.hostname or "localhost"
    port = p.port or (443 if p.scheme == "https" else 80)
    return host, port


def _backend_reachable(url: str, timeout: float = 1.0) -> bool:
    host, port = _host_port(url)
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


@pytest.fixture(scope="session")
def api_client() -> APIClient:
    """One client per session; tests should call ``describe_auth()`` to see state."""
    return APIClient()


@pytest.fixture(scope="session")
def backend_up() -> bool:
    return _backend_reachable(CONFIG.api_base_url)


@pytest.fixture
def require_backend(backend_up: bool) -> None:
    if not backend_up:
        pytest.skip(
            f"Backend not reachable at {CONFIG.api_base_url}. "
            "Start it (e.g., docker-compose up / python manage.py runserver) "
            "or set API_BASE_URL to a running staging URL."
        )


@pytest.fixture
def require_token(api_client: APIClient) -> APIClient:
    if not api_client.token:
        pytest.skip("API_TOKEN not set in .env.test; authenticated tests skipped.")
    return api_client


@pytest.fixture
def detector() -> Detector:
    return Detector()


@pytest.fixture(scope="session")
def metrics_sink():
    return SINK


@pytest.fixture(scope="session")
def reports_dir() -> Path:
    """Session-scoped handle to tests/reports/. Tests can write to it freely."""
    p = CONFIG.reports_dir
    p.mkdir(parents=True, exist_ok=True)
    return p


@pytest.fixture(scope="session")
def sample_alerts() -> list[dict]:
    """Reusable labeled alert set for sweep tests.

    This is the canonical sample used by both the explainability sweep and
    the judge rubric tests. The Judge suite also has its own conftest
    defining one — pytest picks the closest in scope for the file being
    tested.
    """
    return [
        {
            "id": synth.alert_id(),
            "agent": "SYN-AGENT-1",
            "provider": "bkash",
            "reason": "Unusual velocity: 12 cash-outs in 4 min (normal 1/5min)",
            "evidence": "12 transactions, total 48,500 BDT in 4 min",
            "confidence": "high",
            "uncertainty": "+/- 10% on amount totals due to provider feed lag",
            "summary": "Requires review — pattern is unusual for this agent",
            "requires_review": True,
        },
        {
            "id": synth.alert_id(),
            "agent": "SYN-AGENT-2",
            "provider": "nagad",
            "reason": "Just-under-threshold structuring observed (7 × 9,900 BDT in 18 min)",
            "evidence": "7 transactions, each 9,900 BDT, repeated recipient field",
            "confidence": "medium",
            "uncertainty": "could be a merchant split — review needed",
            "summary": "Requires review — possible transaction splitting",
            "requires_review": True,
        },
        {
            "id": synth.alert_id(),
            "agent": "SYN-AGENT-3",
            "provider": "rocket",
            "reason": "Balance feed stale — last successful update 47 min ago",
            "evidence": "feed_lag_minutes=47, fallback estimated",
            "confidence": "low",
            "uncertainty": "values shown are estimates",
            "summary": "Warning — data freshness is degraded",
            "requires_review": True,
        },
        {
            "id": synth.alert_id(),
            "agent": "SYN-AGENT-4",
            "provider": "bkash",
            "reason": "Cross-provider imbalance — short on bKash, surplus on Nagad",
            "evidence": "bkash_available=12,000 BDT; nagad_available=180,000 BDT; demand_expected=145,000 BDT next 60 min",
            "confidence": "medium",
            "uncertainty": "demand forecast +/-15% based on past 14 days",
            "summary": "Requires review — possible intra-day liquidity rebalance",
            "requires_review": True,
        },
    ]


def record_metric(key: str, value, method: str = "") -> None:
    """Convenience wrapper so test files can record without importing SINK."""
    SINK.set(key, value, method)


def pytest_sessionfinish(session, exitstatus):  # noqa: ANN001
    """Dump the metrics report at the end of every run."""
    try:
        paths = SINK.write()
        log.info("Wrote metrics report: %s", paths["json"])
        log.info("Wrote scorecard:     %s", paths["md"])
        log.info("Auth summary: %s", f"base={CONFIG.api_base_url} token={mask_token(CONFIG.api_token)}")
    except Exception as exc:  # pragma: no cover - best-effort cleanup
        log.error("Failed to write metrics report: %s", exc)


def pytest_addoption(parser):  # noqa: ANN001
    parser.addoption(
        "--write-metrics",
        action="store_true",
        default=True,
        help="Always write metrics_report.json at session end (default on).",
    )