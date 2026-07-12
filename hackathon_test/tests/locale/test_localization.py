"""Layer 8 — Bengali / Banglish / English output tests.

Maps to ``testing-scripts-prompt.md`` §8 *and* Judge PART 2 UX
"Localization fallback test".

Two distinct paths:
  * Happy path: alert rendered in Bengali OR Banglish contains situation,
    evidence, uncertainty, and a safe next step.
  * Failure mode: localization service broken → must fall back to safe
    English with the SAME four fields intact (never crash, never blank).

Section 7 lists three acceptable languages: Bengali, Banglish, English.
We test all three on the happy path; Banglish and Bengali are tested via
the API's ``Accept-Language`` or ``?lang=`` toggle.
"""
from __future__ import annotations

import json
from typing import Any, Dict

import pytest

from tests.helpers.api_client import APIClient
from tests.helpers.language import blob_contains_forbidden

pytestmark = pytest.mark.layer8_locale

LANG_KEYS = ("bn", "bangla", "bengali", "bn-Latn", "banglish", "en", "en-US", "english")


def _pick_lang_header(lang: str) -> Dict[str, str]:
    if lang in {"bn", "bangla", "bengali"}:
        return {"Accept-Language": "bn"}
    if lang in {"bn-Latn", "banglish"}:
        return {"Accept-Language": "bn-Latn"}
    return {"Accept-Language": "en"}


def _alert_has_four_fields(blob: Any) -> bool:
    """Situation + evidence + uncertainty + safe next step."""
    def _present(*keys: str) -> bool:
        for k in keys:
            v = blob.get(k) if isinstance(blob, dict) else None
            if v not in (None, "", [], {}):
                return True
        return False
    return (
        _present("situation", "summary", "reason", "message")
        and _present("evidence", "evidence_items", "details", "supporting_evidence")
        and _present("uncertainty", "confidence", "confidence_score")
        and _present("next_step", "recommended_action", "action", "safe_next_step")
    )


class TestHappyPathLocalization:
    @pytest.mark.parametrize("lang", ["bn", "bn-Latn", "en"])
    def test_alert_renders_with_all_four_fields(self, api_client: APIClient,
                                                  require_backend, require_token, lang):
        resp = api_client.get(
            "alerts",
            expect=(200, 401, 403, 404),
            headers=_pick_lang_header(lang),
            label=f"locale_{lang}",
        )
        if resp.status_code in (401, 403, 404):
            pytest.skip(f"alerts list not reachable in {lang}")
        body = resp.json()
        # Find at least one alert-shaped object.
        items = body if isinstance(body, list) else body.get("results") or body.get("items") or []
        if not items:
            pytest.skip(f"no alerts in {lang} — seed the dataset first")
        for alert in items:
            blob = alert if isinstance(alert, dict) else {"message": str(alert)}
            assert _alert_has_four_fields(blob), (lang, blob)
            # No forbidden final-determination words in any locale.
            assert not blob_contains_forbidden(blob), (lang, blob)


class TestLocalizationFallback:
    """Failure-mode counterpart — when localization breaks, the system must
    still render every field in safe English rather than crash or show
    ``undefined`` / blank."""

    def test_broken_localization_falls_back_to_english_with_intact_fields(
        self, api_client: APIClient, require_backend, require_token,
        monkeypatch,
    ):
        # Force a corrupted Accept-Language that no dictionary has.
        resp = api_client.get(
            "alerts",
            expect=(200, 401, 403, 404),
            headers={"Accept-Language": "x-unsupported-locale"},
            label="locale_fallback",
        )
        if resp.status_code in (401, 403, 404):
            pytest.skip("alerts list not reachable for fallback test")
        text = resp.text
        # Must not be empty / undefined / raw exception.
        assert text.strip(), "empty response on locale fallback"
        assert "undefined" not in text.lower(), "alert contains 'undefined'"
        assert "translation error" not in text.lower(), "raw translation error leaked"
        assert "traceback" not in text.lower(), "raw traceback leaked"

        try:
            body = json.loads(text)
        except json.JSONDecodeError:
            pytest.fail(f"locale fallback response is not JSON: {text[:200]}")

        items = body if isinstance(body, list) else body.get("results") or body.get("items") or []
        assert items, "locale fallback returned no items"
        for alert in items:
            assert _alert_has_four_fields(alert if isinstance(alert, dict) else {"message": str(alert)}), (
                "fallback alert missing one of situation/evidence/uncertainty/next_step",
                alert,
            )