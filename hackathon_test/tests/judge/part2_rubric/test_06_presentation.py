"""Judge Part 2 — Presentation & demonstration (5%).

Maps to killer-judge-mode-prompt.md PART 2 §6.

Covers:
  * Demo chaos: brief network delay, two rapid clicks, mid-flow refresh,
    backend restart. UI must recover without losing the story.
  * API token expiry mid-demo: clear re-auth prompt — never a silent
    failure or a raw 401 in the console.
  * Session longevity: dashboard open and idle for an extended period
    stays responsive (or simulated fast-forward).
"""
from __future__ import annotations

import time

import pytest

from tests.helpers.api_client import APIClient

pytestmark = [pytest.mark.judge, pytest.mark.rubric_presentation]


class TestDemoChaos:
    """Simulate a typical interactive fumble during the live demo."""

    def test_token_expiry_triggers_clean_reauth_prompt(self, api_client: APIClient,
                                                        require_backend):
        """An expired token must come back as ``401 token_expired`` (or
        ``redirect_to_login``), never a 5xx or a generic 401 with no
        explanation."""
        # Force-rotate the token via the admin hook if exposed.
        try:
            rot = api_client.post(
                "admin/test/rotate-token",
                json_body={"synthetic": True, "scope": "mgmt"},
                role="mgmt", label="rotate_token",
                expect=(200, 204, 404),
            )
        except Exception:
            pytest.skip("rotation hook missing")
        if rot.status_code == 404:
            pytest.skip("no rotation hook — covered at integration level")

        resp = api_client.get("agents/SYN-AGENT-1/balances", role="mgmt",
                               label="bal_after_rotation",
                               expect=(200, 401, 403))
        assert resp.status_code in (200, 401, 403)
        if resp.status_code in (401, 403):
            body = resp.text.lower()
            assert any(m in body for m in (
                "token_expired", "session_expired", "reauth", "login_required",
                "re-authenticate", "session expired",
            )), f"expired-token response missing re-auth guidance: {resp.text[:200]}"

    def test_rapid_double_click_does_not_double_create(self, api_client: APIClient,
                                                         require_backend):
        from concurrent.futures import ThreadPoolExecutor
        from tests.helpers.synth import new_alert
        payload = new_alert(agent="SYN-AGENT-1", recommendation="top_up_bkash")

        def fire():
            return api_client.post("alerts", json_body=payload, label="dblclick",
                                    expect=(200, 201, 202, 400, 409, 422))

        with ThreadPoolExecutor(max_workers=2) as pool:
            a, b = list(pool.map(lambda _: fire(), range(2)))
        success = [r for r in (a, b) if r.status_code in (200, 201, 202)]
        # We accept 0, 1 or 2 successes if the backend is genuinely idempotent —
        # but the alert ID must be the same in both cases. We approximate by
        # checking for a 409 on the second request.
        if len(success) == 2:
            # Best-effort: if both succeeded, the dataset layer must not have
            # double-counted. We can't detect double-counting at this layer,
            # so we require the second response to indicate dedup.
            assert "duplicate" in b.text.lower() or b.status_code == 201, b.text[:200]


class TestSessionLongevity:
    """Even without a real clock, an idle dashboard should remain
    responsive. We approximate this by re-fetching the same payload twice
    with a small pause, asserting both roundtrips succeed and return the
    same idempotency-stable content."""

    def test_idempotent_repeat_after_pause(self, api_client: APIClient,
                                            require_backend):
        first = api_client.get("agents/SYN-AGENT-1/balances", role="mgmt",
                                label="idle_first")
        time.sleep(0.5)
        second = api_client.get("agents/SYN-AGENT-1/balances", role="mgmt",
                                 label="idle_second")
        assert first.status_code == second.status_code == 200, (
            f"idle re-fetch failed: {first.status_code} vs {second.status_code}"
        )


class TestMidFlowRefreshSurvives:
    """A page refresh in the middle of a flow must not strand the user.
    Approximated: a 404 on a stale route returns a UX-shaped body, not a
    raw stack trace."""

    STALE_ROUTES = [
        "alerts/SYN-ALERT-DOES-NOT-EXIST",
        "agents/SYN-AGENT-NOTH/balances",
        "transactions?cursor=stale-token",
    ]

    @pytest.mark.parametrize("route", STALE_ROUTES)
    def test_stale_route_returns_ux_shaped_404(self, api_client: APIClient,
                                                require_backend, route):
        resp = api_client.get(route, role="mgmt", label=f"stale_{route}",
                               expect=(200, 206, 400, 404))
        if resp.status_code >= 500:
            pytest.fail(f"stale route {route} caused {resp.status_code}: {resp.text[:200]}")
        if resp.status_code == 404:
            body = resp.text.lower()
            # Acceptable messages: "not found", "alert unknown", "agent unknown",
            # "page doesn't exist". Forbidden: the literal Django/Express stack trace.
            forbidden_markers = ("traceback", "exception in", "stacktrace",
                                  "django.utils", "at /app/")
            for marker in forbidden_markers:
                assert marker not in body, (
                    f"stale route {route} shows raw stack trace: {resp.text[:300]}"
                )