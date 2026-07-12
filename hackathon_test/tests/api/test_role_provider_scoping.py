"""Layer 3 — Same-role, different-provider scoping tests.

Maps to ``testing-scripts-prompt.md`` §3 *and* Judge PART 1 #2 "sneaky variant".

Two Ops users at Provider A and Provider B respectively must NOT share
queues/cases even though they have the same job title and same permission
level. This is the gap teams most commonly miss.

If your token doesn't carry a ``provider`` claim (e.g., a generic super-admin
test token), these tests are skipped — wire provider-scoped tokens into
.env.test to make them run.
"""
from __future__ import annotations

import json
import os
from typing import Optional

import pytest
import requests

from tests.helpers.api_client import APIClient
from tests.helpers.env import CONFIG

pytestmark = [pytest.mark.layer3_api, pytest.mark.judge_p1]

# Tokens for the same-role-different-provider test. Read from env at runtime
# — never hardcoded. Absence skips the test cleanly.
PROVIDER_A_TOKEN_ENV = "API_TOKEN_PROVIDER_A"
PROVIDER_B_TOKEN_ENV = "API_TOKEN_PROVIDER_B"


def _read_token(name: str) -> Optional[str]:
    return os.environ.get(name) or None


@pytest.fixture(scope="module")
def provider_a_token() -> Optional[str]:
    return _read_token(PROVIDER_A_TOKEN_ENV)


@pytest.fixture(scope="module")
def provider_b_token() -> Optional[str]:
    return _read_token(PROVIDER_B_TOKEN_ENV)


@pytest.fixture
def provider_a_client(provider_a_token: Optional[str]) -> Optional[APIClient]:
    if not provider_a_token:
        return None
    return APIClient(token=provider_a_token)


@pytest.fixture
def provider_b_client(provider_b_token: Optional[str]) -> Optional[APIClient]:
    if not provider_b_token:
        return None
    return APIClient(token=provider_b_token)


# ---------- IDOR-style: cross-provider direct access ------------------------

class TestCrossProviderIDOR:
    def test_ops_A_cannot_read_provider_B_case(self, provider_a_client: Optional[APIClient],
                                                require_backend):
        if provider_a_client is None:
            pytest.skip(f"Set {PROVIDER_A_TOKEN_ENV} to enable cross-provider scoping tests.")
        # Try to read a case/alert that belongs to Provider B.
        resp = provider_a_client.get("alerts/SYN-ALERT-PROV-B-CASE-1",
                                      expect=(200, 401, 403, 404),
                                      label="idor_cross_provider_read")
        # Acceptable: explicit 403 (preferred) or 404. A bare 200 returning the
        # body would be a Section 14 violation.
        assert resp.status_code in (401, 403, 404), resp.text[:200]

    def test_ops_A_cannot_act_on_provider_B_case(self, provider_a_client: Optional[APIClient],
                                                  require_backend):
        if provider_a_client is None:
            pytest.skip(f"Set {PROVIDER_A_TOKEN_ENV} to enable cross-provider scoping tests.")
        resp = provider_a_client.post("alerts/SYN-ALERT-PROV-B-CASE-1/acknowledge",
                                       expect=(200, 201, 202, 401, 403, 404),
                                       json_body={"actor_note": "test"},
                                       label="idor_cross_provider_act")
        assert resp.status_code in (401, 403, 404), resp.text[:200]


# ---------- Sneaky variant: same-role, different-provider, no parameter tampering

class TestSameRoleDifferentProvider:
    """Clicking through the normal Ops workflow at Provider A must not surface
    Provider B's queue. This is the gap most teams miss — they test IDOR via
    parameter tampering but skip the normal-role flow.
    """

    def test_ops_A_queue_does_not_contain_provider_B_alerts(self,
                                                             provider_a_client: Optional[APIClient],
                                                             require_backend):
        if provider_a_client is None:
            pytest.skip(f"Set {PROVIDER_A_TOKEN_ENV} to enable same-role scoping tests.")
        resp = provider_a_client.get("alerts", expect=(200, 401, 403),
                                      label="ops_A_alerts_list")
        assert resp.status_code == 200, resp.text[:200]
        try:
            body = resp.json()
        except json.JSONDecodeError:
            pytest.fail("ops alerts list is not JSON")
        # Assert no alert that explicitly belongs to Provider B is included.
        if isinstance(body, dict):
            items = body.get("results", body.get("items", body.get("alerts", [])))
        else:
            items = body
        text = json.dumps(items).lower()
        assert "syn-alert-prov-b" not in text, "Provider B alert leaked into Provider A's queue"
        assert "provider_b" not in text, "provider_b tag leaked into Provider A's queue"

    def test_ops_A_dashboard_aggregate_excludes_B(self,
                                                   provider_a_client: Optional[APIClient],
                                                   require_backend):
        if provider_a_client is None:
            pytest.skip(f"Set {PROVIDER_A_TOKEN_ENV} to enable same-role scoping tests.")
        resp = provider_a_client.get("dashboard/aggregate", expect=(200, 401, 403),
                                      label="ops_A_aggregate")
        if resp.status_code == 404:
            pytest.skip("aggregate dashboard endpoint not implemented yet")
        assert resp.status_code == 200, resp.text[:200]
        body_text = resp.text.lower()
        assert "provider_b" not in body_text, "provider_b leaked into aggregate dashboard"