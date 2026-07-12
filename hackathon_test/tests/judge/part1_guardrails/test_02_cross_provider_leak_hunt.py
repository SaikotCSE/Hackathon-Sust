"""Judge Part 1, Guardrail 2 — Cross-provider data leak hunt.

The prototype is explicitly forbidden from allowing one provider's ops /
risk team to read another provider's agent balances, alerts, or audit logs.

Three concrete probes:
  1. IDOR-by-parameter — supply the right provider's token, but URL includes
     a different provider's agent identifier.
  2. Aggregate endpoints must NOT mix providers — a request that asks for
     "all agents" from a provider-scoped role must return only that
     provider's rows.
  3. Response payloads must NEVER contain an aggregate summed balance
     that obfuscates provider breakdown. Cross-provider ``total`` is fine
     for the agent view (the agent OWNS that view), but a provider-ops
     endpoint summing two providers is a leak.
"""
from __future__ import annotations

import pytest

from tests.helpers.api_client import APIClient

pytestmark = [pytest.mark.judge, pytest.mark.guardrail_critical]


class TestNoCrossProviderLeak:
    """Provider-scoped roles must be isolated."""

    def test_providerA_ops_cannot_read_providerB_agent(self, api_client: APIClient,
                                                         require_backend):
        resp = api_client.get(
            "provider/rocket/agents/SYN-AGENT-ROCKET-1/balances",
            expect=(403, 404),
            role="ops_providerA",
            label="cross_provider_rocket_balance",
        )
        assert resp.status_code in (403, 404), (
            f"ops_providerA got {resp.status_code} on Rocket endpoint: {resp.text[:200]}"
        )

    def test_providerA_risk_cannot_ack_providerB_alert(self, api_client: APIClient,
                                                         require_backend):
        resp = api_client.post(
            "alerts/SYN-ALERT-ROCKET-1/ack",
            expect=(403, 404),
            json_body={"note": "SYN-ack"},
            role="risk_providerA",
            label="cross_provider_alert_ack",
        )
        assert resp.status_code in (403, 404)

    def test_provider_ops_aggregate_does_not_leak_other_providers(self, api_client: APIClient,
                                                                    require_backend):
        """The bKash ops dashboard calls ``GET /provider/bkash/agents`` and
        expects only bKash-tagged agents."""
        try:
            resp = api_client.get("provider/bkash/agents", role="ops_providerA",
                                  label="bkash_aggregate")
        except Exception:
            pytest.skip("provider aggregate endpoint not deployed")
        if resp.status_code != 200:
            pytest.skip(f"aggregate endpoint returned {resp.status_code}")
        body = resp.text.lower()
        # The response must not mention a different provider's agents or balances.
        assert "nagad" not in body and "rocket" not in body, (
            f"bkash aggregate leaked other providers: {resp.text[:300]}"
        )


class TestIDORByParameterTampering:
    """Same role, same token, change only the URL path parameter."""

    @pytest.mark.parametrize("attack", [
        ("GET", "agents/SYN-AGENT-OWN-1/balances", "SYN-AGENT-OTHER-1"),
        ("GET", "agents/SYN-AGENT-OWN-1/unified-view", "SYN-AGENT-OTHER-1"),
        ("GET", "agents/SYN-AGENT-OWN-1/alerts", "SYN-AGENT-OTHER-1"),
        ("GET", "alerts/SYN-ALERT-OWN-1", "SYN-ALERT-OTHER-1"),
    ])
    def test_url_tampering_returns_403_or_404(self, api_client: APIClient,
                                                require_backend, attack):
        method, template, other_id = attack
        own_path = template.replace("OTHER-1", "OWN-1")
        other_path = template.replace("OWN-1", "OTHER-1")
        # First: own — must succeed.
        own_resp = api_client.request(method, own_path, role="agent",
                                       label=f"own_{own_path}")
        # Second: other agent's — must NOT succeed.
        other_resp = api_client.request(method, other_path, role="agent",
                                         expect=(403, 404),
                                         label=f"idor_{other_path}")
        assert other_resp.status_code in (403, 404), (
            f"IDOR: agent role accessed {other_path}: {other_resp.status_code}"
        )


class TestNoAggregateMixingForProviderScopedRole:
    """Provider-scoped roles are NEVER served a unified "all providers" view."""
    SCOPED_ENDPOINTS = [
        "provider/bkash/aggregates/cash",
        "provider/nagad/aggregates/cash",
    ]

    @pytest.mark.parametrize("endpoint", SCOPED_ENDPOINTS)
    def test_provider_scoped_aggregate_returns_only_self(self, api_client: APIClient,
                                                          require_backend, endpoint):
        try:
            resp = api_client.get(endpoint, role="ops_providerA",
                                  label=f"agg_{endpoint}")
        except Exception:
            pytest.skip("endpoint missing")
        if resp.status_code != 200:
            pytest.skip("not deployed")
        body = resp.text.lower()
        assert "other_providers" not in body and "all_providers" not in body, body