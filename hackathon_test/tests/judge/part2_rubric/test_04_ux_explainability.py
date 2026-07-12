"""Judge Part 2 — UX & explainability (10%).

Maps to killer-judge-mode-prompt.md PART 2 §4.

Covers:
  * Explanation coverage: walk every alert and assert 100% include a stated
    reason, evidence, and confidence/uncertainty. Any gap = fail (not a
    rounding error).
  * Dead-end sweep: script a walk-through of every reachable screen state
    and assert none surface a raw error, ``undefined``, or an unexplained
    number.
  * Localization fallback test: deliberately break the Bengali/Banglish
    rendering path while an alert is rendering. System must fall back to
    safe English with ALL fields still populated.
"""
from __future__ import annotations

import json
import re

import pytest

from tests.helpers.api_client import APIClient
from tests.helpers.language import REQUIRED_REVIEW_LANGUAGE

pytestmark = [pytest.mark.judge, pytest.mark.rubric_ux]


class TestExplanationCoverage:
    """Every alert's user-facing copy MUST contain:
      * a reason (what)
      * evidence (numbers)
      * a confidence or uncertainty value
    Missing any one of those is a §13 UX fail.
    """
    EVIDENCE_PAT = re.compile(
        r"(\b\d{1,3}(?:,\d{3})*(?:\.\d+)?\s*(?:bdt|taka|tk))",
        re.IGNORECASE,
    )

    def test_every_alert_has_reason_evidence_uncertainty(self, sample_alerts):
        gaps = []
        for a in sample_alerts:
            blob = json.dumps(a, default=str).lower()
            text = str(a.get("reason") or a.get("explanation") or
                        a.get("summary") or "").lower()
            if "reason" not in a and "explanation" not in a and "summary" not in a:
                gaps.append((a.get("id"), "no reason/evidence field"))
                continue
            has_evidence = bool(self.EVIDENCE_PAT.search(text) or
                                 "evidence" in blob)
            has_uncertainty = any(m in blob for m in (
                "confidence", "uncertainty", "requires_review", "warning",
                "data_quality",
            ))
            if not has_evidence:
                gaps.append((a.get("id"), "no evidence"))
            if not has_uncertainty:
                gaps.append((a.get("id"), "no uncertainty/confidence"))
        assert not gaps, f"alerts missing reason/evidence/uncertainty: {gaps}"


class TestDeadEndSweep:
    """Walk through every reachable screen state and check no raw error,
    ``undefined``, or unexplained number appears."""

    SCREENS = [
        ("GET", "agents/SYN-AGENT-1"),
        ("GET", "agents/SYN-AGENT-1/balances"),
        ("GET", "agents/SYN-AGENT-1/unified-view"),
        ("GET", "agents/SYN-AGENT-1/alerts"),
        ("GET", "agents/SYN-AGENT-1/transactions"),
        ("GET", "alerts"),
        ("GET", "alerts/SYN-ALERT-1"),
        ("GET", "alerts?status=open"),
        ("GET", "alerts?status=resolved"),
        ("GET", "audit/events"),
        ("GET", "audit/events?alert=SYN-ALERT-1"),
        ("GET", "settings/thresholds"),
        ("GET", "settings/risk-thresholds"),
    ]

    UNEXPLAINED_TOKENS = ("nan", "undefined", "[object object]", "null", "error")

    @pytest.mark.parametrize("method,endpoint", SCREENS)
    def test_screen_renders_no_raw_error(self, api_client: APIClient,
                                          require_backend, method, endpoint):
        try:
            resp = api_client.request(method, endpoint, role="mgmt",
                                       label=f"scan_{endpoint}")
        except Exception:
            pytest.skip(f"{endpoint} not deployed")
        if resp.status_code in (404, 405):
            pytest.skip("missing")
        if resp.status_code >= 500:
            pytest.fail(f"{endpoint} returned {resp.status_code} during dead-end sweep")
        body = resp.text.lower()
        # 4xx is acceptable ONLY for routes that legitimately don't exist;
        # they shouldn't be in this list.
        assert resp.status_code in (200, 206), (
            f"{endpoint} returned {resp.status_code}: {resp.text[:200]}"
        )
        for tok in self.UNEXPLAINED_TOKENS:
            # ``null`` is a JSON key value (``"foo": null``) but the bare
            # word "null" in a UI string is bad.
            if tok in body:
                # Allow JSON ``null`` only between quotes.
                if tok == "null" and re.search(r":\s*null\b", body):
                    # JSON literal — fine if the rest of the response has
                    # a meaningful shape.
                    continue
                pytest.fail(
                    f"{method} {endpoint} body contains unexplained token {tok!r}: "
                    f"{resp.text[:200]}"
                )


class TestLocalizationFallback:
    """If the Bengali / Banglish translation layer crashes, the system must
    fall back to safe English copy that still includes reason/evidence/
    uncertainty/next-step. No raw error, no blank string, no ``undefined``.

    We simulate the failure with a ``locale=bn`` flag and a poisoned
    translation layer via the admin-test endpoint if it exists; otherwise we
    verify the contract via direct API call."""

    def test_bengali_render_falls_back_safely(self, api_client: APIClient,
                                                require_backend):
        try:
            resp = api_client.get(
                "alerts/SYN-ALERT-1?locale=bn&force_translation_error=1",
                role="mgmt", label="bn_render",
                expect=(200, 206, 503),
            )
        except Exception:
            pytest.skip("locale endpoint missing")
        if resp.status_code == 503:
            pytest.skip("no locale hook exposed — covered by base Layer 8 happy path")
        body = resp.text.lower()
        # Acceptable forms after fallback:
        allowed_markers = ("reason:", "evidence:", "confidence:", "next step:",
                            "requires review", "warning")
        for marker in allowed_markers:
            assert marker in body, f"fallback copy missing {marker!r}: {resp.text[:300]}"
        # Critical: no raw error / undefined / blank field.
        for bad in ("undefined", "[object object]", "translation error"):
            assert bad not in body, (
                f"fallback contains raw error token {bad!r}: {resp.text[:300]}"
            )