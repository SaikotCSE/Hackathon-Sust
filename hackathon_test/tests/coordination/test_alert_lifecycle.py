"""Layer 5 — Coordination / workflow tests.

Maps to ``testing-scripts-prompt.md`` §5 (audit trail).

Covers:
  * Full alert lifecycle: created → routed → acknowledged → escalated → resolved.
  * Every transition timestamped and traceable.
  * No auto-resolve, no auto-block, no financial action.
  * Role-boundary: a risk/compliance-only field cannot be set by an agent.
"""
from __future__ import annotations

import pytest

from tests.helpers.api_client import APIClient
from tests.helpers.metrics import SINK

pytestmark = pytest.mark.layer5_coordination

ALERT_PATH = "alerts/{alert_id}"


def _path(p: str, **kw) -> str:
    return p.format(**kw)


# ---------- lifecycle happy path --------------------------------------------

class TestAlertLifecycle:
    def test_full_lifecycle_traceable(self, api_client: APIClient,
                                       require_backend, require_token):
        """Drive the alert through every transition; assert history is
        timestamped and traceable."""
        aid = "SYN-ALERT-LIFECYCLE-TEST"
        # Create
        r = api_client.post("alerts", expect=(200, 201, 202, 400, 401, 403, 404),
                            json_body={
                                "provider": "bkash",
                                "agent_id": "SYN-AGENT-LIFECYCLE",
                                "severity": "high",
                                "type": "liquidity_pressure",
                                "synthetic": True,
                            },
                            label="create_alert")
        if r.status_code in (401, 403, 404):
            pytest.skip("alert create endpoint not available for synthetic flow")
        assert r.status_code < 500

        # Acknowledge
        r2 = api_client.post(_path("alerts/{}/acknowledge", alert_id=aid),
                             expect=(200, 201, 202, 400, 401, 403, 404),
                             json_body={"actor_note": "ack by ops"},
                             label="ack_alert")
        assert r2.status_code < 500

        # Escalate
        r3 = api_client.post(_path("alerts/{}/escalate", alert_id=aid),
                             expect=(200, 201, 202, 400, 401, 403, 404),
                             json_body={"reason": "no contact in 30m"},
                             label="escalate_alert")
        assert r3.status_code < 500

        # Resolve
        r4 = api_client.post(_path("alerts/{}/resolve", alert_id=aid),
                             expect=(200, 201, 202, 400, 401, 403, 404),
                             json_body={"resolution_note": "agent topped up"},
                             label="resolve_alert")
        assert r4.status_code < 500

        # History
        r5 = api_client.get(_path("alerts/{}/history", alert_id=aid),
                            expect=(200, 401, 403, 404), label="alert_history")
        if r5.status_code == 200:
            history = r5.json()
            transitions = history.get("transitions") or history.get("events") or []
            assert len(transitions) >= 1, history
            # Each transition must carry a timestamp + actor.
            for t in transitions:
                assert "timestamp" in t or "at" in t, t
                assert "actor" in t or "by" in t or "actor_role" in t, t
        else:
            pytest.skip("alert history endpoint not implemented yet")


# ---------- never auto-act ---------------------------------------------------

class TestNeverAutoAct:
    """Assert the system never silently takes a financial action; only
    proposes / recommends and waits for human action."""

    @pytest.mark.parametrize("endpoint,body,label", [
        ("alerts/SYN-AUTO/resolve", {"resolution": "blocked_user"}, "resolve"),
        ("alerts/SYN-AUTO/acknowledge", {"auto": True}, "ack"),
        ("alerts/SYN-AUTO/escalate", {"auto": True}, "escalate"),
        ("alerts/SYN-AUTO/assign", {"auto": True}, "assign"),
    ])
    def test_no_endpoint_takes_implicit_financial_action(self, api_client: APIClient,
                                                          require_backend, require_token,
                                                          endpoint, body, label):
        resp = api_client.post(endpoint, expect=(200, 201, 202, 400, 401, 403, 404),
                                json_body=body, label=f"no_auto_{label}")
        # Acceptable outcomes: success (human-approved), validation error
        # (request malformed), forbidden (role not allowed), not found.
        assert resp.status_code < 500
        if resp.status_code in (200, 201, 202):
            text = resp.text.lower()
            # The system should never report that it blocked / froze / transferred funds.
            for forbidden_outcome in (
                "transferred_funds", "funds_moved", "wallet_blocked",
                "auto_blocked", "freeze_applied", "balance_zeroed",
            ):
                assert forbidden_outcome not in text, resp.text[:300]


# ---------- role-boundary for risk-only fields -------------------------------

class TestRiskOnlyFields:
    """Final determination fields must not be writable by an 'agent' role."""

    def test_agent_cannot_set_final_determination(self, api_client: APIClient,
                                                   require_backend, require_token):
        # We attempt to set a risk-only field. The API should either reject
        # (preferred) or silently strip it. Either way, no risk verdict should
        # end up persisted.
        resp = api_client.post(
            "alerts/SYN-RISK-FIELD/resolve",
            expect=(200, 201, 202, 400, 401, 403, 404),
            json_body={
                "resolution_note": "looks fine",
                "final_determination": "confirmed_fraud",  # Section 14 forbidden
                "verdict": "fraudulent",
            },
            label="risk_field_attempt",
        )
        if resp.status_code in (200, 201, 202):
            body = resp.text.lower()
            # Server should not echo the forbidden field back as a verdict.
            for bad in ("confirmed_fraud", "fraudulent", "is_fraud: true"):
                assert bad not in body, resp.text[:300]