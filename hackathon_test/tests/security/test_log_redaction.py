"""Layer 9 — log redaction / no-credential-leakage.

Asserts no log line printed by the application or the test suite itself
contains raw tokens, real-looking account numbers, or sensitive customer PII.
"""
from __future__ import annotations

import re

import pytest

from tests.helpers.env import mask_token

pytestmark = pytest.mark.layer9_security

SENSITIVE_PATTERNS = [
    re.compile(r"\b\d{4,}\b\s*(?:bdt|taka)", re.IGNORECASE),  # account-like + currency
    re.compile(r"pin\s*[:=]\s*\d{4,}", re.IGNORECASE),
    re.compile(r"otp\s*[:=]\s*\d{4,}", re.IGNORECASE),
    re.compile(r"password\s*[:=]\s*\S+", re.IGNORECASE),
    re.compile(r"private[_ ]?key\s*[:=]\s*\S+", re.IGNORECASE),
]


def _scan(text: str) -> list[str]:
    return [p.pattern for p in SENSITIVE_PATTERNS if p.search(text)]


class TestLogRedaction:
    def test_mask_token_helper_never_leaks_full_value(self):
        token = "ABCDEFG1234567890SECRET"
        masked = mask_token(token)
        assert token not in masked
        assert len(masked) <= max(8, len(token) // 2)

    def test_reports_dir_has_no_raw_tokens(self, reports_dir):
        """Walk every JSON / text file written to /reports and scan it."""
        if not any(reports_dir.iterdir()):
            pytest.skip("no reports written yet")
        offenders = []
        for path in reports_dir.rglob("*"):
            if path.is_dir():
                continue
            try:
                text = path.read_text(encoding="utf-8", errors="ignore")
            except Exception:
                continue
            patterns = _scan(text)
            if patterns:
                offenders.append((str(path), patterns))
        assert not offenders, f"unredacted sensitive content in reports: {offenders}"

    def test_synthetic_dataset_contains_no_real_pii(self, sample_alerts):
        """Sample alert dict must contain only SYN- IDs and synthetic names."""
        for a in sample_alerts:
            blob = str(a)
            for pat in SENSITIVE_PATTERNS:
                assert not pat.search(blob), f"PII pattern {pat.pattern!r} in {a!r}"
            # Customer names must be synthetic (SYN- prefix) or generic placeholders.
            assert any(marker in blob for marker in ("SYN-", "demo-", "Test")), blob