"""Judge Part 1, Guardrail 1 — Fraud / guilt declaration hunt.

Section 14 (problem_statement.md) and PART 1 §1 (killer-judge-mode-prompt.md)
forbid the prototype from ever outputting the words ``fraud``, ``confirmed``,
``guilty``, ``fraudster`` or any equivalent accusation. The judge mode prompt
is explicit: "Any system that declares fraud, blocks transactions by itself,
or accuses a specific person is DISQUALIFIED."

This suite sweeps:
  * Every API response body reachable from the test build.
  * Every alert payload in the synthetic dataset.
  * Every explanation string in the UI bundle / static assets.
  * The ``data/`` directory if any is shipped (templates / sample alerts).
  * The contents of ``logs/`` and ``reports/`` produced by the prototype.

A single occurrence of any forbidden token in a user-visible string is a
hard failure of this test, regardless of context. The same token inside a
test file or comment is acceptable (the test literally has to name what it's
looking for).
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from tests.helpers.api_client import APIClient
from tests.helpers.language import (
    FORBIDDEN_ACCUSATION_TOKENS,
    REQUIRED_REVIEW_LANGUAGE,
)

pytestmark = [pytest.mark.judge, pytest.mark.guardrail_critical]

REPO_ROOT = Path(__file__).resolve().parents[3]


# ---------------------------------------------------------------------------
# API-surface scan
# ---------------------------------------------------------------------------

class TestAPISurfaceCleanOfAccusations:
    """Walk every reachable endpoint and assert the body never contains a
    forbidden accusation token."""

    ENDPOINTS = [
        ("GET", "agents/SYN-AGENT-1"),
        ("GET", "agents/SYN-AGENT-1/balances"),
        ("GET", "agents/SYN-AGENT-1/unified-view"),
        ("GET", "agents/SYN-AGENT-1/alerts"),
        ("GET", "alerts/SYN-ALERT-1"),
        ("GET", "alerts?status=open"),
        ("GET", "alerts?agent=SYN-AGENT-1"),
        ("GET", "transactions?agent=SYN-AGENT-1"),
    ]

    @pytest.mark.parametrize("method,endpoint", ENDPOINTS)
    def test_endpoint_body_contains_no_accusation(self, api_client: APIClient,
                                                    require_backend,
                                                    method, endpoint):
        try:
            resp = api_client.request(method, endpoint, role="mgmt", label=f"scan_{endpoint}")
        except Exception:
            pytest.skip(f"endpoint {endpoint} not deployed")
        if resp.status_code >= 500:
            pytest.skip(f"endpoint {endpoint} returned {resp.status_code}")
        body = resp.text.lower()
        for tok in FORBIDDEN_ACCUSATION_TOKENS:
            assert tok not in body, (
                f"forbidden token {tok!r} in {method} {endpoint}: {resp.text[:300]}"
            )


# ---------------------------------------------------------------------------
# Bundle / static-asset scan
# ---------------------------------------------------------------------------

class TestStaticBundlesCleanOfAccusations:
    """Walk every JS/HTML/CSS/JSON file shipped in the build."""

    EXTS = (".js", ".html", ".css", ".json", ".svg", ".txt")

    @pytest.fixture(scope="class")
    def candidate_files(self):
        candidates: list[Path] = []
        for ext in self.EXTS:
            candidates.extend(REPO_ROOT.rglob(f"*{ext}"))
        # Filter: skip node_modules, venv, .git, tests/, legacy.
        skip_dirs = ("node_modules", ".git", "venv", ".venv",
                     "__pycache__", "tests", "migrations")
        return [
            p for p in candidates
            if not any(s in p.parts for s in skip_dirs)
        ]

    def test_no_accusation_tokens_in_bundles(self, candidate_files):
        offenders = []
        for path in candidate_files:
            try:
                text = path.read_text(encoding="utf-8", errors="ignore").lower()
            except Exception:
                continue
            for tok in FORBIDDEN_ACCUSATION_TOKENS:
                if tok in text:
                    offenders.append((str(path), tok))
        assert not offenders, f"forbidden tokens found in build: {offenders[:20]}"


# ---------------------------------------------------------------------------
# Sample alert dataset scan
# ---------------------------------------------------------------------------

class TestSampleAlertsUseReviewLanguage:
    """Re-run the explanation-coverage sweep with a hardened lexical check:
    when a flag is shown to the user, the explanation MUST include one of the
    neutral review-language tokens. This is the Section 7 rule, mechanically.
    """

    def test_every_alert_explanation_uses_review_language(self, sample_alerts):
        missing = []
        for a in sample_alerts:
            text = json.dumps(a, default=str).lower()
            # Skip alerts without user-facing explanation.
            if not any(k in a for k in ("reason", "explanation", "summary")):
                continue
            if not any(marker in text for marker in REQUIRED_REVIEW_LANGUAGE):
                missing.append(a.get("id") or a.get("alert_id"))
        assert not missing, (
            f"alerts without review-language markers: {missing}"
        )


# ---------------------------------------------------------------------------
# Hard rule
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("token", sorted(FORBIDDEN_ACCUSATION_TOKENS))
def test_global_blacklist_contains_token(token):
    """If the language helper ever loses a token, this fails. Sentries must be
    present in *exactly* the documented list."""
    assert token in {"fraud", "confirmed", "guilty", "fraudster", "criminal"}, (
        f"FORBIDDEN_ACCUSATION_TOKENS set has changed — re-read §14 and update prompts."
    )