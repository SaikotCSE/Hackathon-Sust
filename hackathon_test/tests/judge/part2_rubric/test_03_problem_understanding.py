"""Judge Part 2 — Problem understanding & ecosystem relevance (15%).

Maps to killer-judge-mode-prompt.md PART 2 §3.

Two checks:
  * Language audit: scan all user-facing text for any phrasing implying one
    provider can see / control another provider's data or balance.
  * "Unusual" / "requires review" language must be used everywhere a flag
    is shown.

If the prototype's UI/strings uses phrasing like "we can check the bKash
balance from Nagad" or "merge bKash and Nagad balances", that is an
ecosystem understanding violation.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from tests.helpers.language import REQUIRED_REVIEW_LANGUAGE, FORBIDDEN_CROSS_PROVIDER_LANGUAGE

pytestmark = [pytest.mark.judge, pytest.mark.rubric_problem_understanding]

REPO_ROOT = Path(__file__).resolve().parents[3]


class TestNoCrossProviderControlLanguage:
    """User-facing copy must not imply one provider can read/control another.

    Forbidden phrases include:
      * "merge Nagad + bKash balance"
      * "the bKash side sees the Nagad balance"
      * "Nagad can query bKash for cash-out feasibility"
    """
    SCAN_DIRS = ("frontend", "ui", "src", "app", "server", "templates")

    @pytest.fixture(scope="class")
    def text_corpus(self):
        chunks = []
        for d in self.SCAN_DIRS:
            base = REPO_ROOT / d
            if not base.exists():
                continue
            for p in base.rglob("*"):
                if not p.is_file():
                    continue
                if any(s in p.parts for s in ("node_modules", ".git", "dist",
                                              "__pycache__", "venv", ".venv")):
                    continue
                if p.suffix not in (".html", ".js", ".ts", ".tsx", ".jsx",
                                    ".md", ".txt", ".po"):
                    continue
                try:
                    text = p.read_text(encoding="utf-8", errors="ignore").lower()
                except Exception:
                    continue
                chunks.append((str(p), text))
        return chunks

    def test_no_cross_provider_control_language(self, text_corpus):
        offenders = []
        for path, text in text_corpus:
            for phrase in FORBIDDEN_CROSS_PROVIDER_LANGUAGE:
                if phrase in text:
                    offenders.append((path, phrase))
        assert not offenders, (
            f"cross-provider control language in user-facing copy: {offenders[:20]}"
        )


class TestAllFlagsUseReviewLanguage:
    """Across all flagged views, the wording uses 'unusual' / 'requires review'."""
    def test_flag_strings_use_review_language(self, sample_alerts):
        for a in sample_alerts:
            # only check alerts with a user-facing summary
            for key in ("summary", "reason", "explanation"):
                if key in a and a[key]:
                    text = str(a[key]).lower()
                    assert any(m in text for m in REQUIRED_REVIEW_LANGUAGE), (
                        f"alert {a.get('id')!r} flag missing review-language marker: {a[key]!r}"
                    )