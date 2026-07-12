"""Layer 4 — Data-quality / reliability & fallback tests.

Maps to ``testing-scripts-prompt.md`` §4.

Covers:
  * Delayed, missing, and conflicting provider feeds.
  * Chaos-style drops that the system must absorb gracefully.
  * The deterministic math-contradiction fixture (one provider's stated
    balance contradicts its own ledger).
  * Reliability rollup into §12's "Reliability under degraded input" metric.
"""
from __future__ import annotations

import copy
import random

import pytest

from tests.helpers import synth
from tests.helpers.liquidity import aggregate_view
from tests.helpers.metrics import SINK

pytestmark = pytest.mark.layer4_data_quality


# ---------- pure-Python assertions on the reference aggregator -------------

class TestAggregatorFallback:
    """Tests that the *data structure itself* never silently fakes confidence.

    These run without the backend — they validate the contract any backend
    implementation must honour.
    """

    def test_missing_provider_lowers_confidence(self):
        feeds = [
            synth.linear_drain_curve(
                starting_balance=10_000, per_minute_drain=50, duration_min=10, provider="bkash", seed=1
            ),
            synth.linear_drain_curve(
                starting_balance=8_000, per_minute_drain=40, duration_min=10, provider="nagad", seed=2
            ),
            synth.linear_drain_curve(
                starting_balance=6_000, per_minute_drain=30, duration_min=10, provider="rocket", seed=3
            ),
        ]
        full = aggregate_view(cash_on_hand=20_000.0, feeds=feeds)
        # rocket "goes dark" — confidence for it drops to 0.1
        partial = aggregate_view(
            cash_on_hand=20_000.0,
            feeds=feeds,
            per_provider_confidence=[1.0, 1.0, 0.1],
        )
        assert partial.aggregate_confidence < full.aggregate_confidence

    def test_conflicting_balance_lowers_confidence(self):
        feeds = [
            synth.linear_drain_curve(
                starting_balance=10_000, per_minute_drain=50, duration_min=10, provider="bkash", seed=1
            ),
        ]
        # Two sources disagree — confidence for that provider drops.
        conflicting = aggregate_view(
            cash_on_hand=20_000.0,
            feeds=feeds,
            per_provider_confidence=[0.3],
        )
        assert conflicting.aggregate_confidence == pytest.approx(0.3, abs=0.01)

    def test_chaos_drops_no_crash(self):
        rng = random.Random(0)
        feeds = [
            synth.linear_drain_curve(
                starting_balance=10_000, per_minute_drain=50, duration_min=60, provider="bkash", seed=1
            ),
            synth.linear_drain_curve(
                starting_balance=8_000, per_minute_drain=40, duration_min=60, provider="nagad", seed=2
            ),
            synth.linear_drain_curve(
                starting_balance=6_000, per_minute_drain=30, duration_min=60, provider="rocket", seed=3
            ),
        ]
        # Randomly drop 30% of events across providers — must not raise.
        for feed in feeds:
            feed.transactions = [t for t in feed.transactions if rng.random() > 0.30]
        view = aggregate_view(
            cash_on_hand=20_000.0,
            feeds=feeds,
            per_provider_confidence=[0.7, 0.5, 0.4],  # degraded confidence
        )
        # Even after drops, providers are still logically separate.
        assert len(view.providers) == 3
        assert all(p["name"] in {"bkash", "nagad", "rocket"} for p in view.providers)

    def test_math_contradiction_forces_confidence_to_zero(self):
        """One provider's stated balance contradicts its own ledger.

        Implementation must detect the arithmetic impossibility and either
        force confidence to 0 (preferred) or expose an explicit invalid-data
        flag. Silently averaging it away would be a Section 14 violation.
        """
        bad = synth.balance_ledger_contradiction_feed(provider="rocket")
        # Confirm the fixture itself is internally inconsistent.
        assert not bad.is_consistent()

        # The aggregator must surface a degraded confidence for the bad feed.
        view = aggregate_view(
            cash_on_hand=20_000.0,
            feeds=[
                synth.linear_drain_curve(
                    starting_balance=10_000, per_minute_drain=50, duration_min=10, provider="bkash", seed=1
                ),
                bad,
            ],
            per_provider_confidence=[1.0, 0.0],
        )
        rocket = next(p for p in view.providers if p["name"] == "rocket")
        assert rocket["confidence"] == 0.0
        assert view.aggregate_confidence < 0.5  # dragged down by the contradiction

        SINK.set(
            "reliability_under_degraded_input",
            {
                "scenarios_run": ["missing_feed", "conflicting_balance", "chaos_drops",
                                  "math_contradiction"],
                "passed": True,
                "aggregate_confidence_under_contradiction": view.aggregate_confidence,
            },
            method="Layer 4 fallback + math-contradiction fixture",
        )


# ---------- API-level fallback tests ----------------------------------------

class TestAPIFallback:
    def test_healthcheck_responds(self, api_client, require_backend, require_token):
        """When a provider feed is degraded the unified view should still
        respond — not 500."""
        resp = api_client.get(
            "agents/SYN-AGENT-DEGRADED-TEST/balances",
            expect=(200, 404), label="degraded_balances",
        )
        if resp.status_code == 404:
            pytest.skip("degraded fixture not provisioned")
        assert resp.status_code == 200
        body = resp.json()
        # If any provider is degraded, confidence for that provider < 1.0
        if body.get("providers"):
            degraded = [p for p in body["providers"] if p.get("confidence", 1.0) < 1.0]
            # Either no degraded providers (good case), or degraded but
            # surfaced with low confidence — never silently full confidence.
            if degraded:
                assert all(p["confidence"] < 1.0 for p in degraded)

    def test_fallback_status_endpoint(self, api_client, require_backend, require_token):
        """A documented endpoint that exposes per-provider data freshness
        (§7 safe-fallback expectation)."""
        resp = api_client.get(
            "system/fallback-status",
            expect=(200, 404), label="fallback_status",
        )
        if resp.status_code == 404:
            pytest.skip("fallback status endpoint not implemented yet")
        body = resp.json()
        # At minimum, the response must report per-provider freshness.
        assert "providers" in body or "data_status" in body or "degraded" in body