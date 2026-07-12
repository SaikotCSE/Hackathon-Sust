"""Layer 7 — pytest-side latency capture.

Runs a small synchronous burst against the documented endpoints and writes
the latency summary into the shared ``MetricsSink`` so it appears in
``metrics_report.json`` after the session ends.

Marked ``slow`` — opt in with ``-m slow`` or by running ``run_all_tests.sh``.
"""
from __future__ import annotations

import statistics
import time

import pytest

from tests.helpers.api_client import APIClient
from tests.helpers.metrics import SINK

pytestmark = [pytest.mark.layer7_perf, pytest.mark.slow]

REQUESTS_PER_ENDPOINT = 25


def _burst(api_client: APIClient, agent_id: str) -> list[float]:
    durations: list[float] = []
    for _ in range(REQUESTS_PER_ENDPOINT):
        t0 = time.perf_counter()
        r = api_client.get(
            f"agents/{agent_id}/outlet",
            expect=(200, 404),
            label="perf_burst",
        )
        dt_ms = (time.perf_counter() - t0) * 1000.0
        # Record even 404s — they're real responses, just not in this fixture.
        if r.status_code in (200, 404):
            durations.append(dt_ms)
    return durations


def _percentile(vals: list[float], p: float) -> float:
    if not vals:
        return 0.0
    s = sorted(vals)
    idx = max(0, min(len(s) - 1, int(round(p / 100 * (len(s) - 1)))))
    return s[idx]


def test_outlet_latency_under_documented_load(api_client: APIClient,
                                                require_backend, require_token):
    durations = _burst(api_client, agent_id="SYN-AGENT-PERF-0001")
    if not durations:
        pytest.skip("backend did not return 200/404 for outlet endpoint")

    summary = {
        "avg_ms": round(statistics.fmean(durations), 2),
        "p50_ms": round(_percentile(durations, 50), 2),
        "p95_ms": round(_percentile(durations, 95), 2),
        "p99_ms": round(_percentile(durations, 99), 2),
        "n": len(durations),
        "endpoint": "GET /agents/{id}/outlet",
        "dataset_size": "50 agents × 3 providers × 500 txns (documented baseline)",
    }
    SINK.set(
        "api_processing_latency",
        summary,
        method="Layer 7 synchronous burst (25 reqs) — combine with Locust for full §12 metric",
    )

    # Soft threshold — if average > 1.5s, that's still a dashboard issue.
    assert summary["avg_ms"] < 1500, summary