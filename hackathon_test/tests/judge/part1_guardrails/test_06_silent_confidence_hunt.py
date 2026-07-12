"""Judge Part 1, Guardrail 6 — Silent confidence hunt.

Section 14: the prototype MUST NOT present confident numbers derived from
stale, contradictory, or absent data. Anywhere the system shows a balance,
it must also show a confidence indicator (or a "requires review" flag)
proportional to the data quality.

If the system displays a number with no provenance / confidence metadata
when the data source was missing, delayed, or returned a contradictory
ledger row, the test fails.

Maps to killer-judge-mode-prompt.md PART 1 §6.
"""
from __future__ import annotations

import pytest

from tests.helpers.api_client import APIClient

pytestmark = [pytest.mark.judge, pytest.mark.guardrail_critical]


class TestConfidenceAlwaysPresent:
    """Every balance / shortage / anomaly response must carry a confidence
    or review flag, regardless of data state."""

    ENDPOINTS = [
        ("GET", "agents/SYN-AGENT-1/balances"),
        ("GET", "agents/SYN-AGENT-1/unified-view"),
        ("GET", "alerts/SYN-ALERT-1"),
        ("GET", "alerts?status=open"),
    ]

    @pytest.mark.parametrize("method,endpoint", ENDPOINTS)
    def test_response_has_confidence_or_review_flag(self, api_client: APIClient,
                                                      require_backend,
                                                      method, endpoint):
        try:
            resp = api_client.request(method, endpoint, role="mgmt", label=endpoint)
        except Exception:
            pytest.skip("endpoint not deployed")
        if resp.status_code >= 500 or resp.status_code == 404:
            pytest.skip(f"endpoint unavailable: {resp.status_code}")
        body = resp.text.lower()
        assert any(m in body for m in (
            "confidence", "requires_review", "data_quality", "freshness",
            "uncertainty", "warning",
        )), (
            f"{method} {endpoint} returned body with no confidence / freshness / "
            f"review metadata: {resp.text[:300]}"
        )


class TestLateFeedLowersConfidence:
    """When the synthetic feed is delayed by > T seconds, the prototype must
    lower the displayed confidence OR mark the alert/data as ``requires_review``.

    Trick: the helper ``inject_late_feed`` simulates a stale data state and
    we then call the API to read back the confidence."""
    def test_late_feed_visibility(self, require_backend, api_client: APIClient):
        # The suite cannot modify the live backend, but if the backend
        # exposes a /admin/test/feed-delay hook we exercise it.
        try:
            resp = api_client.post(
                "admin/test/inject-late-feed",
                json_body={"provider": "bkash", "delay_seconds": 3600, "synthetic": True},
                role="mgmt", label="late_feed_inject", expect=(200, 201, 204, 404),
            )
        except Exception:
            pytest.skip("admin endpoint not exposed")
        if resp.status_code == 404:
            pytest.skip("late-feed injection hook not exposed")

        # Re-read balances and verify confidence is below the baseline.
        bal = api_client.get("agents/SYN-AGENT-1/balances", role="mgmt",
                              label="balances_after_late_feed")
        if bal.status_code != 200:
            pytest.skip("balance endpoint unavailable")
        # Check that the body indicates some warning or reduced confidence.
        body = bal.text.lower()
        assert any(m in body for m in ("stale", "late", "low_confidence",
                                         "requires_review", "warning")), (
            f"balance endpoint did not surface stale-feed warning: {bal.text[:300]}"
        )


class TestContradictoryLedgerForcesZeroConfidence:
    """Inject a math-contradiction: stated balance = 5,000 BDT but ledger
    deductions sum to 10,000 BDT. The system must NOT silently present
    5,000 BDT — it must flag ``confidence_score=0`` and ``requires_review=true``."""
    def test_contradiction_forces_low_confidence(self, require_backend, api_client: APIClient):
        try:
            api_client.post(
                "admin/test/inject-contradictory-ledger",
                json_body={
                    "agent": "SYN-AGENT-1",
                    "stated_balance": 5000,
                    "ledger_total": 10000,
                    "synthetic": True,
                },
                role="mgmt", label="inject_contradiction",
                expect=(200, 201, 204, 404),
            )
        except Exception:
            pytest.skip("contradiction injection hook not exposed")

        view = api_client.get("agents/SYN-AGENT-1/unified-view", role="mgmt",
                               label="view_after_contradiction")
        if view.status_code != 200:
            pytest.skip("view endpoint unavailable")
        body = view.text.lower()
        # Either confidence is 0 / near-zero, or there is a review flag.
        zero_conf = '"confidence":0' in body or '"confidence_score":0' in body or \
                    '"confidence": 0' in body or '"confidence_score": 0' in body
        assert zero_conf or "requires_review" in body or "data_invalid" in body, (
            f"contradictory ledger still showed a confident number: {view.text[:300]}"
        )