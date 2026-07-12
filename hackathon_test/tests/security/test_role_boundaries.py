"""Layer 9 — role-based access boundaries.

Cross-tenant isolation, no privilege escalation, no horizontal IDOR.
"""
from __future__ import annotations

import pytest

from tests.helpers.api_client import APIClient

pytestmark = pytest.mark.layer9_security


class TestNoPrivilegeEscalation:
    """An agent-role token must never be able to act as ops / risk / mgmt."""

    @pytest.mark.parametrize("escalation_endpoint", [
        "alerts/SYN-ALERT-1/resolve",       # risk-only
        "alerts/SYN-ALERT-1/escalate",      # risk / mgmt
        "agents/SYN-AGENT-1/freeze",       # ops / risk only
        "agents/SYN-AGENT-1/reassign",      # mgmt only
        "settings/risk-thresholds",         # mgmt only
        "audit/export",                     # mgmt only
    ])
    def test_agent_token_blocked_from_escalated_endpoints(self, api_client: APIClient,
                                                            require_backend,
                                                            escalation_endpoint):
        """The agent role (lowest privilege) MUST receive 403 for any endpoint
        reserved for a higher role. A 200 here is a guardrail violation."""
        resp = api_client.post(
            escalation_endpoint,
            expect=(403, 404, 405),
            json_body={"note": "SYN-note-from-agent"},
            role="agent",
            label=f"agent_escalation_{escalation_endpoint}",
        )
        assert resp.status_code in (403, 404, 405), (
            f"agent role got {resp.status_code} from {escalation_endpoint}: {resp.text[:200]}"
        )


class TestHorizontalIdor:
    """Two agent tokens — agent A cannot read agent B's detail or balance."""

    def test_agent_cannot_read_other_agents_balances(self, api_client: APIClient,
                                                      require_backend):
        resp = api_client.get(
            "agents/SYN-AGENT-2/balances",
            expect=(403, 404),
            role="agent",
            label="idor_cross_agent",
        )
        assert resp.status_code in (403, 404), (
            f"cross-agent read succeeded: {resp.status_code} {resp.text[:200]}"
        )

    def test_ops_providerA_cannot_read_providerB_ops_endpoint(self, api_client: APIClient,
                                                              require_backend):
        resp = api_client.get(
            "provider/rocket/agents",  # bKash ops token attempting Rocket endpoint
            expect=(403, 404),
            role="ops_providerA",
            label="idor_cross_provider_ops",
        )
        assert resp.status_code in (403, 404), (
            f"cross-provider ops read succeeded: {resp.status_code} {resp.text[:200]}"
        )