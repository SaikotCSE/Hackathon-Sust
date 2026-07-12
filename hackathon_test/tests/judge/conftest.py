"""Judge-only conftest.

Wires up a session-scoped sample_alerts fixture that the rubric tests
reuse, and registers a unique ``reports/`` directory just for the judge
suite.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from tests.helpers import synth

REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="session")
def sample_alerts() -> list[dict]:
    """A diverse, realistic, labeled sample of alerts.

    The set is deterministic (no randomness) so the rubric coverage test
    is reproducible.
    """
    base = [
        {
            "id": synth.alert_id(),
            "agent": "SYN-AGENT-1",
            "provider": "bkash",
            "reason": "Unusual velocity: 12 cash-outs in 4 minutes (normal: 1 / 5 min)",
            "evidence": "12 transactions, total 48,500 BDT in 4 min window",
            "confidence": "high",
            "uncertainty": "±10% on amount totals due to provider feed lag",
            "summary": "Requires review — pattern is unusual for this agent",
            "requires_review": True,
        },
        {
            "id": synth.alert_id(),
            "agent": "SYN-AGENT-2",
            "provider": "nagad",
            "reason": "Just-under-threshold structuring: 7 × 9,900 BDT pieces within 18 min",
            "evidence": "7 transactions, each 9,900 BDT, recipient field repeating",
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
            "uncertainty": "values shown are estimates; live figure unavailable",
            "summary": "Warning — data freshness is degraded for this agent",
            "requires_review": True,
        },
        {
            "id": synth.alert_id(),
            "agent": "SYN-AGENT-4",
            "provider": "bkash",
            "reason": "Cross-provider imbalance — agent short on bKash but surplus on Nagad",
            "evidence": "bkash_available=12,000 BDT; nagad_available=180,000 BDT; demand_expected=145,000 BDT in next 60 min",
            "confidence": "medium",
            "uncertainty": "demand forecast ±15% based on past 14 days; weekend uplift not modelled",
            "summary": "Requires review — possible intra-day liquidity rebalance opportunity",
            "requires_review": True,
        },
    ]
    return base


@pytest.fixture(scope="session")
def reports_dir() -> Path:
    p = REPO_ROOT / "tests" / "reports"
    p.mkdir(parents=True, exist_ok=True)
    return p
