"""Language / risk-language guards.

Holds the universal forbidden-word list (Section 7 mandatory language rule +
Section 14 guardrails). Layer 2 (anomaly), Layer 6 (explainability) and
Judge PART 1 #1 all import from here so the rule is enforced in one place.
"""
from __future__ import annotations

import re
from typing import Any, Iterable, List

# Section 7: "user review" + "careful language" — risk signals must never be
# labelled as fraud / confirmed. Section 14 reinforces.
FORBIDDEN_FINAL_DETERMINATION_WORDS = (
    "fraud", "fraudulent", "guilty", "confirmed fraud",
    "confirmed-fraud", "confirmed_fraud", "final determination",
)

# A flag should be "unusual" / "requires review".
REQUIRED_SAFE_PHRASES = (
    "unusual",
    "requires review",
)

# Aliases — newer tests use these names.
FORBIDDEN_ACCUSATION_TOKENS = FORBIDDEN_FINAL_DETERMINATION_WORDS + ("fraudster",
                                                                       "criminal")
REQUIRED_REVIEW_LANGUAGE = REQUIRED_SAFE_PHRASES + ("review", "warning", "anomaly")

# Section 7 / Section 14: do not claim one provider can see or control
# another provider's data.
FORBIDDEN_CROSS_PROVIDER_LANGUAGE = (
    "merge bkash and nagad balance",
    "merge nagad and bkash balance",
    "merge bkash and rocket balance",
    "merge rocket and bkash balance",
    "merge nagad and rocket balance",
    "merge rocket and nagad balance",
    "the nagad side sees the bkash balance",
    "the bkash side sees the nagad balance",
    "the rocket side sees the bkash balance",
    "the bkash side sees the rocket balance",
    "the rocket side sees the nagad balance",
    "the nagad side sees the rocket balance",
    "rocket can query bkash for cash-out feasibility",
    "nagad can query bkash for cash-out feasibility",
    "bkash can query nagad for cash-out feasibility",
    "bkash can query rocket for cash-out feasibility",
    "move funds from bkash to nagad",
    "move funds from nagad to bkash",
    "transferred from bkash to nagad automatically",
    "transfer from bkash to nagad automatically",
    "auto-rebalance bkash and nagad",
    "auto-rebalance nagad and bkash",
    "auto-rebalance bkash and rocket",
    "auto-rebalance rocket and bkash",
    "auto-rebalance nagad and rocket",
    "auto-rebalance rocket and nagad",
    "balance pooling bkash nagad",
    "balance pooling bkash rocket",
    "balance pooling nagad rocket",
)

# Fields that should never appear in any request/response schema.
SENSITIVE_FIELD_TOKENS = (
    "pin",
    "otp",
    "password",
    "passwd",
    "private_key",
    "secret_key",
    "card_number",
    "cvv",
    "ssn",
    "account_number",
)


def _stringify_blob(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, (list, tuple)):
        return "\n".join(_stringify_blob(v) for v in value)
    if isinstance(value, dict):
        parts = []
        for k, v in value.items():
            parts.append(f"{k}: {_stringify_blob(v)}")
        return "\n".join(parts)
    return str(value)


def blob_contains_forbidden(value: Any, words: Iterable[str] = FORBIDDEN_FINAL_DETERMINATION_WORDS) -> List[str]:
    """Return every forbidden word found in any string field of ``value``."""
    text = _stringify_blob(value).lower()
    hits = []
    for w in words:
        if re.search(rf"\b{re.escape(w.lower())}\b", text):
            hits.append(w)
    return hits


def alert_is_safely_phrased(alert: Any) -> bool:
    """An alert passes only if (a) no forbidden word AND (b) required phrases present."""
    blob = _stringify_blob(alert).lower()
    forbidden_present = any(
        re.search(rf"\b{re.escape(w.lower())}\b", blob)
        for w in FORBIDDEN_FINAL_DETERMINATION_WORDS
    )
    required_present = all(
        re.search(re.escape(w.lower()), blob) for w in REQUIRED_SAFE_PHRASES
    )
    return (not forbidden_present) and required_present