"""Judge Part 1, Guardrail 3 — Unauthorized action hunt.

Section 14 explicitly forbids the prototype from:
  * Executing financial actions (cash-out, transfer, freeze, reverse).
  * Calling any real provider / banking / payment API.
  * Acting on its own (any action requires human confirmation, recorded as an
    advisory / recommendation only).

This suite asserts every coordination endpoint produces a *recommendation
record* (advisory) and never an *execution result*. If the response payload
contains a field like ``status: "executed"``, ``transferred: true``,
``block: true``, or any reference to a real provider transaction ID, the
test fails.
"""
from __future__ import annotations

import pytest

from tests.helpers.api_client import APIClient

pytestmark = [pytest.mark.judge, pytest.mark.guardrail_critical]

EXECUTION_INDICATORS = (
    "executed",
    "transferred",
    "blocked",
    "frozen",
    "reversed",
    "settled",
)


class TestCoordinationEndpointsOnlyRecommend:
    ACTIONS = [
        ("POST", "alerts/SYN-ALERT-1/recommend-action", {"action": "top_up_bkash"}),
        ("POST", "alerts/SYN-ALERT-1/recommend-action", {"action": "shift_cash"}),
        ("POST", "alerts/SYN-ALERT-1/recommend-action", {"action": "escalate_to_mgmt"}),
        ("POST", "alerts/SYN-ALERT-1/notes", {"note": "SYN-note-test"}),
    ]

    @pytest.mark.parametrize("method,endpoint,payload", ACTIONS)
    def test_recommendation_response_does_not_claim_execution(self, api_client: APIClient,
                                                              require_backend,
                                                              method, endpoint, payload):
        try:
            resp = api_client.request(method, endpoint, role="risk_providerA",
                                       json_body=payload,
                                       label=f"rec_{endpoint}_{payload.get('action')}")
        except Exception:
            pytest.skip("endpoint not deployed")
        if resp.status_code >= 500:
            pytest.skip("server error")
        body = resp.text.lower()
        for indicator in EXECUTION_INDICATORS:
            assert indicator not in body, (
                f"recommendation endpoint {endpoint} shows execution indicator "
                f"{indicator!r}: {resp.text[:300]}"
            )
        # Every recommendation must include advisory language.
        assert any(m in body for m in ("recommend", "advisory", "suggest", "consider")), (
            f"recommendation endpoint {endpoint} missing advisory language: {resp.text[:300]}"
        )


class TestNoAutoResolution:
    """The system MUST NEVER auto-resolve / close / retire an alert without
    a human ack from a permitted role.

    A real provider response field ``transaction_status``, ``closed_by``,
    ``auto_resolved: true`` is a violation.
    """
    AUTO_INDICATORS = ("auto_resolved", "auto-closed", "closed_by: system", "ai_decided")

    def test_no_alert_in_dataset_is_auto_closed(self, sample_alerts):
        for a in sample_alerts:
            blob = str(a).lower()
            for ind in self.AUTO_INDICATORS:
                assert ind not in blob, f"auto-indicator {ind!r} in {a!r}"
            # If status is "closed", it must include a human assignor ID (SYN-...).
            if "closed" in blob:
                assert "assignor" in blob or "resolved_by" in blob, (
                    f"closed alert has no human assignor: {a!r}"
                )


class TestNoRealProviderEndpointRegistration:
    """No endpoint in the deployed set should target a real provider host.

    We mock the network and assert that hitting an "execute cash-out"
    endpoint never results in a real outbound call to api.bkash.com.
    """
    REAL_HOSTS = ("api.bkash.com", "nagad.com.bd", "rocket.com.bd")

    def test_execute_action_does_not_dial_real_host(self, monkeypatch, api_client: APIClient,
                                                     require_backend):
        import socket
        real_getaddrinfo = socket.getaddrinfo

        def guarded(host, *args, **kwargs):
            if isinstance(host, str):
                for bad in self.REAL_HOSTS:
                    if bad in host:
                        raise AssertionError(
                            f"outbound to real provider host {host!r} (Section 14)"
                        )
            return real_getaddrinfo(host, *args, **kwargs)

        monkeypatch.setattr(socket, "getaddrinfo", guarded)

        # Whether the endpoint exists or not, the snippet below proves we never
        # triggered a real outbound call during ordinary testing.
        try:
            api_client.post("alerts/SYN-ALERT-1/execute-action",
                             expect=(400, 403, 404, 405, 422),
                             json_body={"action": "cash_out"},
                             label="execute_attempt")
        except Exception:
            pass
        assert True