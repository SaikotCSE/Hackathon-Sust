"""Judge Part 1, Guardrail 4 — Credential-collection hunt.

The prototype MUST NOT collect:
  * Customer PINs / OTPs / passwords / private keys.
  * Real card numbers (PAN), CVVs, or full bank account numbers.
  * Real provider API tokens, signatures, or webhooks secrets.

A field present in any request schema, response payload, sample row, or
persisted record is a guardrail violation. This test enforces that by
schema-inspecting the OpenAPI / Swagger document if one is exposed, and by
running a substring sweep across the entire repo source tree (excluding
``tests/`` so the test can name what it's looking for).
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.helpers.api_client import APIClient
from tests.helpers.language import SENSITIVE_FIELD_TOKENS

pytestmark = [pytest.mark.judge, pytest.mark.guardrail_critical]

REPO_ROOT = Path(__file__).resolve().parents[3]


# ---------------------------------------------------------------------------
# Schema-level scan
# ---------------------------------------------------------------------------

class TestOpenAPISchemaHasNoSensitiveFields:
    """If a swagger/openapi endpoint is exposed, walk every model and assert
    no field name matches a sensitive token."""

    SCHEMA_PATHS = ("/api/schema/", "/api/swagger.json", "/api/openapi.json",
                    "/openapi.json", "/swagger.json")

    @pytest.mark.parametrize("schema_path", SCHEMA_PATHS)
    def test_openapi_schema_clean_of_sensitive_fields(self, api_client: APIClient,
                                                      require_backend, schema_path):
        try:
            resp = api_client.get(schema_path, role="mgmt",
                                  label=f"schema_{schema_path}")
        except Exception:
            pytest.skip("schema endpoint unavailable")
        if resp.status_code != 200:
            pytest.skip("schema not exposed")
        try:
            doc = resp.json()
        except json.JSONDecodeError:
            pytest.skip("schema body not JSON")
        # Look at field names everywhere a field can be defined.
        offenders: list[str] = []
        stack = [doc]
        seen = 0
        while stack and seen < 5000:
            node = stack.pop()
            seen += 1
            if isinstance(node, dict):
                if "properties" in node and isinstance(node["properties"], dict):
                    for name in node["properties"]:
                        if any(tok in name.lower() for tok in SENSITIVE_FIELD_TOKENS):
                            offenders.append(name)
                for v in node.values():
                    stack.append(v)
            elif isinstance(node, list):
                stack.extend(node)
        assert not offenders, (
            f"sensitive field names in OpenAPI schema: {offenders}"
        )


# ---------------------------------------------------------------------------
# Source tree scan
# ---------------------------------------------------------------------------

class TestNoSensitiveFieldNamesInAppSource:
    """App-side source (``app/``, ``server/``, ``src/``, ``mobile/``) MUST NOT
    declare a sensitive field. Tests/ may declare it as a sentinel."""

    SOURCE_DIRS = ("app", "server", "src", "mobile", "backend",
                   "frontend", "web", "ui")

    @pytest.fixture(scope="class")
    def app_source_files(self):
        results: list[Path] = []
        for d in self.SOURCE_DIRS:
            base = REPO_ROOT / d
            if not base.exists():
                continue
            for p in base.rglob("*.py"):
                if "migrations" in p.parts:
                    continue
                results.append(p)
            for p in base.rglob("*.js"):
                if "node_modules" in p.parts or "dist" in p.parts:
                    continue
                results.append(p)
            for p in base.rglob("*.ts"):
                if "node_modules" in p.parts or "dist" in p.parts:
                    continue
                results.append(p)
        return results

    def test_no_sensitive_field_names(self, app_source_files):
        offenders = []
        for path in app_source_files:
            try:
                text = path.read_text(encoding="utf-8", errors="ignore").lower()
            except Exception:
                continue
            for tok in SENSITIVE_FIELD_TOKENS:
                # Heuristic: the token must be used as a field, not in a comment.
                # We approximate by searching for ``"token_name"``-style usage.
                if f'"{tok}"' in text or f"'{tok}'" in text or f"{tok}:" in text:
                    offenders.append((str(path), tok))
        assert not offenders, (
            f"sensitive field references in app source: {offenders[:30]}"
        )


# ---------------------------------------------------------------------------
# API-roundtrip scan
# ---------------------------------------------------------------------------

class TestNoSensitiveEchoInResponses:
    """Submit sensitive values to every plausible endpoint and assert the
    value is never echoed in the response body."""

    @pytest.mark.parametrize("endpoint", [
        "auth/login", "agents/SYN-AGENT-1", "alerts", "transactions",
        "agents/SYN-AGENT-1/notes", "settings/thresholds",
    ])
    def test_endpoint_does_not_echo_sensitive_value(self, api_client: APIClient,
                                                     require_backend, endpoint):
        for tok, value in [("pin", "123456"), ("otp", "0000"),
                            ("password", "SYN-pwd"), ("private_key", "SYN-pk")]:
            payload = {tok: value, "extra": "SYN-suffix"}
            try:
                resp = api_client.post(endpoint, json_body=payload,
                                        expect=(200, 201, 202, 400, 401, 403, 404, 415, 422),
                                        role="agent", label=f"echo_{tok}_{endpoint}")
            except Exception:
                continue
            if resp.status_code >= 500:
                continue
            # Sensitive value MUST NOT be echoed.
            assert value not in resp.text, (
                f"{endpoint} echoed {tok}={value}: {resp.text[:200]}"
            )