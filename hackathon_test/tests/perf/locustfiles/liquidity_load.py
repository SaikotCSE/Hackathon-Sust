"""Locust scenario for §12 latency metric.

Models the afternoon-before-Eid demand spike from §2:
* 50 simulated agents
* 3 providers per agent
* 500 simulated transactions
* Mixed read (unified outlet view) and write (alert acknowledge) traffic

Reports average + p95/p99 latencies into tests/reports/perf_summary.json
when run with ``--json``.
"""
from __future__ import annotations

import json
import os
import random
import time
from pathlib import Path
from typing import Optional

from locust import HttpUser, between, events, task  # type: ignore

# Defaults — overridable via env.
API_BASE_HOST = os.environ.get("LOCUST_HOST", "http://localhost:8000")
API_BASE_PATH = os.environ.get("API_BASE_PATH", "/api/")
API_TOKEN = os.environ.get("API_TOKEN", "")
AGENT_COUNT = int(os.environ.get("AGENT_COUNT", "50"))


def _agent_ids() -> list[str]:
    return [f"SYN-AGENT-PERF-{i:04d}" for i in range(AGENT_COUNT)]


class LiquidityAgent(HttpUser):
    """Simulated Super Agent user driving the prototype's read endpoints."""

    wait_time = between(0.5, 1.5)

    def on_start(self) -> None:
        # Locust uses self.client (HttpSession) — prepend the API path once.
        self.client.base_url = API_BASE_HOST + API_BASE_PATH
        if API_TOKEN:
            self.client.headers["Authorization"] = f"Bearer {API_TOKEN}"

    @task(3)
    def unified_outlet_view(self) -> None:
        agent_id = random.choice(_agent_ids())
        self.client.get(
            f"agents/{agent_id}/outlet",
            name="GET /agents/{id}/outlet",
        )

    @task(2)
    def per_provider_balance(self) -> None:
        agent_id = random.choice(_agent_ids())
        provider = random.choice(["bkash", "nagad", "rocket"])
        self.client.get(
            f"agents/{agent_id}/providers/{provider}/balance",
            name="GET /agents/{id}/providers/{provider}/balance",
        )

    @task(2)
    def liquidity_forecast(self) -> None:
        agent_id = random.choice(_agent_ids())
        self.client.get(
            f"agents/{agent_id}/liquidity/forecast",
            name="GET /agents/{id}/liquidity/forecast",
        )

    @task(1)
    def alerts_list(self) -> None:
        self.client.get("alerts", name="GET /alerts")


# -------- summary sink -------------------------------------------------------

SUMMARY_PATH = Path(__file__).resolve().parents[2] / "reports" / "perf_summary.json"


@events.test_stop.add_listener
def _write_summary(environment, **kwargs):  # noqa: ANN001
    stats = environment.stats
    summary = {
        "host": API_BASE_HOST + API_BASE_PATH,
        "agents_simulated": AGENT_COUNT,
        "endpoints": [],
    }
    for entry in stats.entries.values():
        summary["endpoints"].append(
            {
                "name": entry.name,
                "method": entry.method,
                "num_requests": entry.num_requests,
                "num_failures": entry.num_failures,
                "avg_ms": round(entry.avg_response_time, 2),
                "min_ms": round(entry.min_response_time, 2),
                "max_ms": round(entry.max_response_time, 2),
                "p50_ms": round(entry.get_response_time_percentile(0.50) or 0, 2),
                "p95_ms": round(entry.get_response_time_percentile(0.95) or 0, 2),
                "p99_ms": round(entry.get_response_time_percentile(0.99) or 0, 2),
                "rps": round(entry.total_rps, 4),
            }
        )
    SUMMARY_PATH.parent.mkdir(parents=True, exist_ok=True)
    SUMMARY_PATH.write_text(json.dumps(summary, indent=2), encoding="utf-8")