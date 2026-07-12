"""Synthetic data generator for the Super Agent Liquidity platform.

Implements the scenarios the problem statement calls out explicitly:

* Scenario A - hidden provider shortage (§11)
* Scenario B - liquidity pressure with unusual activity (§11)
* Scenario C - cross-provider / data inconsistency (§11)
* Scenario D - coordinated response / closure (§11)
* Pre-Eid rush modelled on §2 ("afternoon before Eid")
* Participant-flexibility patterns from §9: repeated amounts, velocity
  spikes, transaction splitting, circular activity, balance inconsistencies.

All identifiers are tagged ``SYN-`` so the synthetic-prefix assertion
(``Layer 9``) can prove no real PII was used.
"""
from __future__ import annotations

import random
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional, Sequence

# Synthetic prefix required by Layer 9 (security smoke) and the
# "Responsible-design note" deliverable in §10.
SYN = "SYN-"

PROVIDERS = ("bkash", "nagad", "rocket")
AREAS = ("mirpur", "uttara", "dhanmondi", "chawkbazar", "tejgaon")


def _tid(prefix: str) -> str:
    return f"{SYN}{prefix}-{uuid.uuid4().hex[:10]}"


def agent_id() -> str:
    return _tid("AGENT")


def transaction_id() -> str:
    return _tid("TXN")


def provider_id(name: str) -> str:
    return f"{SYN}PROV-{name.upper()}"


@dataclass
class SyntheticProviderFeed:
    """A time-ordered stream of events for one provider + agent."""

    provider: str
    opening_balance: float
    current_balance: float
    transactions: List[Dict[str, Any]] = field(default_factory=list)
    status_flags: List[str] = field(default_factory=list)

    def is_consistent(self) -> bool:
        """Sum of (cash_in - cash_out) should reconcile against the delta."""
        delta_observed = sum(
            t["amount"] if t["direction"] == "cash_in" else -t["amount"]
            for t in self.transactions
        )
        delta_expected = self.current_balance - self.opening_balance
        return abs(delta_observed - delta_expected) < 0.5


def linear_drain_curve(
    *,
    starting_balance: float,
    per_minute_drain: float,
    duration_min: int,
    provider: str = "bkash",
    seed: int = 0,
) -> SyntheticProviderFeed:
    """Steady linear drain — Scenario A's canonical shape."""
    rng = random.Random(seed)
    txns = []
    balance = starting_balance
    now = datetime.now(timezone.utc)
    for minute in range(duration_min):
        amount = per_minute_drain + rng.uniform(-per_minute_drain * 0.1, per_minute_drain * 0.1)
        amount = round(max(amount, 0.0), 2)
        if balance - amount < 0:
            break
        balance -= amount
        txns.append(
            {
                "id": transaction_id(),
                "timestamp": (now + timedelta(minutes=minute)).isoformat(),
                "amount": amount,
                "direction": "cash_out",
                "provider": provider,
                "agent_id": agent_id(),
                "area": rng.choice(AREAS),
            }
        )
    return SyntheticProviderFeed(
        provider=provider,
        opening_balance=starting_balance,
        current_balance=round(balance, 2),
        transactions=txns,
    )


def pre_eid_burst(
    *,
    starting_balance: float,
    burst_per_min: float,
    duration_min: int,
    provider: str = "bkash",
    seed: int = 1,
) -> SyntheticProviderFeed:
    """Quadratic ramp — models the afternoon-before-Eid demand spike (§2)."""
    rng = random.Random(seed)
    txns = []
    balance = starting_balance
    now = datetime.now(timezone.utc)
    for m in range(duration_min):
        ramp = burst_per_min * (1 + (m / duration_min) ** 2)
        amount = round(ramp + rng.uniform(-5, 5), 2)
        balance -= amount
        txns.append(
            {
                "id": transaction_id(),
                "timestamp": (now + timedelta(minutes=m)).isoformat(),
                "amount": max(amount, 0.0),
                "direction": "cash_out",
                "provider": provider,
                "agent_id": agent_id(),
                "area": rng.choice(AREAS),
            }
        )
    return SyntheticProviderFeed(
        provider=provider,
        opening_balance=starting_balance,
        current_balance=round(balance, 2),
        transactions=txns,
    )


def salary_day_normal(
    *,
    n: int = 200,
    amount_band: tuple = (5_000.0, 10_000.0),
    accounts_pool: int = 80,
    seed: int = 7,
) -> List[Dict[str, Any]]:
    """Legitimate high-volume salary-disbursement day (false-positive baseline)."""
    rng = random.Random(seed)
    base = datetime.now(timezone.utc).replace(hour=9, minute=0, second=0, microsecond=0)
    out: List[Dict[str, Any]] = []
    for _ in range(n):
        out.append(
            {
                "id": transaction_id(),
                "timestamp": (base + timedelta(seconds=rng.randint(0, 4 * 3600))).isoformat(),
                "amount": round(rng.uniform(*amount_band), 2),
                "direction": "cash_out",
                "provider": rng.choice(PROVIDERS),
                "customer_id": _tid("CUST"),
                "agent_id": agent_id(),
                "area": rng.choice(AREAS),
                "label": "salary_disbursement_legit",
            }
        )
    return out


def eid_pre_holiday_normal(
    *,
    n: int = 250,
    seed: int = 11,
) -> List[Dict[str, Any]]:
    """Legitimate Eid cash-out surge (false-positive baseline, §9)."""
    rng = random.Random(seed)
    base = datetime.now(timezone.utc).replace(hour=14, minute=0, second=0, microsecond=0)
    out: List[Dict[str, Any]] = []
    for _ in range(n):
        out.append(
            {
                "id": transaction_id(),
                "timestamp": (base + timedelta(seconds=rng.randint(0, 2 * 3600))).isoformat(),
                "amount": round(rng.uniform(1_000.0, 25_000.0), 2),
                "direction": "cash_out",
                "provider": rng.choice(PROVIDERS),
                "customer_id": _tid("CUST"),
                "agent_id": agent_id(),
                "area": rng.choice(AREAS),
                "label": "eid_cashout_legit",
            }
        )
    return out


def structuring_pattern(
    *,
    target_amount: float = 49_000.0,
    per_chunk: float = 9_800.0,
    provider: str = "nagad",
    n_chunks: int = 5,
    seed: int = 13,
) -> List[Dict[str, Any]]:
    """One large amount broken into just-under-threshold pieces (Section 9)."""
    rng = random.Random(seed)
    base = datetime.now(timezone.utc)
    return [
        {
            "id": transaction_id(),
            "timestamp": (base + timedelta(minutes=rng.randint(0, 30))).isoformat(),
            "amount": per_chunk,
            "direction": "cash_out",
            "provider": provider,
            "customer_id": _tid("CUST"),
            "agent_id": agent_id(),
            "area": rng.choice(AREAS),
            "label": "structuring_injected",
            "is_anomaly": True,
        }
        for _ in range(n_chunks)
    ]


def near_identical_repeats(
    *,
    amount: float = 12_500.0,
    n: int = 12,
    provider: str = "bkash",
    unique_customers: int = 1,
    seed: int = 17,
) -> List[Dict[str, Any]]:
    """Section 9's 'repeated or near-identical amounts' injected pattern."""
    rng = random.Random(seed)
    base = datetime.now(timezone.utc)
    return [
        {
            "id": transaction_id(),
            "timestamp": (base + timedelta(seconds=i * 30)).isoformat(),
            "amount": amount + rng.uniform(-50, 50),
            "direction": "cash_out",
            "provider": provider,
            "customer_id": _tid("CUST"),
            "agent_id": agent_id(),
            "area": rng.choice(AREAS),
            "label": "near_identical_repeats_injected",
            "is_anomaly": True,
        }
        for i in range(n)
    ]


def velocity_spike(
    *,
    amount_band: tuple = (500.0, 1_500.0),
    n: int = 60,
    window_seconds: int = 120,
    provider: str = "rocket",
    seed: int = 19,
) -> List[Dict[str, Any]]:
    """Section 9's 'unusual transaction velocity' injected pattern."""
    rng = random.Random(seed)
    base = datetime.now(timezone.utc)
    return [
        {
            "id": transaction_id(),
            "timestamp": (base + timedelta(seconds=rng.randint(0, window_seconds))).isoformat(),
            "amount": round(rng.uniform(*amount_band), 2),
            "direction": "cash_out",
            "provider": provider,
            "customer_id": _tid("CUST"),
            "agent_id": agent_id(),
            "area": rng.choice(AREAS),
            "label": "velocity_spike_injected",
            "is_anomaly": True,
        }
        for _ in range(n)
    ]


def circular_activity(
    *,
    amount: float = 5_000.0,
    hops: int = 4,
    accounts: int = 3,
    seed: int = 23,
) -> List[Dict[str, Any]]:
    """Section 9's 'circular activity' injected pattern."""
    rng = random.Random(seed)
    base = datetime.now(timezone.utc)
    custs = [_tid("CUST") for _ in range(accounts)]
    out: List[Dict[str, Any]] = []
    for h in range(hops):
        out.append(
            {
                "id": transaction_id(),
                "timestamp": (base + timedelta(minutes=h * 5)).isoformat(),
                "amount": amount,
                "direction": "cash_out",
                "provider": rng.choice(PROVIDERS),
                "customer_id": custs[h % len(custs)],
                "agent_id": agent_id(),
                "area": rng.choice(AREAS),
                "label": "circular_injected",
                "is_anomaly": True,
            }
        )
    return out


def balance_ledger_contradiction_feed(
    *,
    provider: str = "rocket",
) -> SyntheticProviderFeed:
    """One provider's stated balance disagrees with its own ledger (Part 1, #6 trap).

    Opening balance 5,000 BDT, then a single ledger entry deducts 10,000 BDT.
    The reconciliation check (``is_consistent``) returns False by definition.
    """
    now = datetime.now(timezone.utc)
    txns = [
        {
            "id": transaction_id(),
            "timestamp": now.isoformat(),
            "amount": 10_000.0,
            "direction": "cash_out",
            "provider": provider,
            "agent_id": agent_id(),
            "area": "dhanmondi",
        }
    ]
    return SyntheticProviderFeed(
        provider=provider,
        opening_balance=5_000.0,
        current_balance=5_000.0,  # balanced on paper; ledger disagrees
        transactions=txns,
        status_flags=["ledger_contradiction_fixture"],
    )


def alert_id() -> str:
    return _tid("ALERT")


def new_alert(*, agent: str = "SYN-AGENT-1", recommendation: str = "top_up_bkash") -> dict:
    """Build a fresh alert dict suitable for POST /alerts payloads."""
    return {
        "id": alert_id(),
        "agent_id": agent,
        "provider": "bkash",
        "reason": f"SYN-test alert recommending {recommendation}",
        "evidence": "SYN-synthetic evidence payload",
        "confidence": "medium",
        "requires_review": True,
        "recommendation": recommendation,
        "summary": f"SYN-test requires review for {recommendation}",
    }


def synth_transaction(*, amount: float, provider: str = "bkash",
                       agent: str = "SYN-AGENT-1", txn_id: str | None = None) -> dict:
    """Single cash-out transaction dict suitable for POST /transactions."""
    return {
        "id": txn_id or transaction_id(),
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "amount": amount,
        "direction": "cash_out",
        "provider": provider,
        "agent_id": agent,
        "customer_id": _tid("CUST"),
        "label": "synth_test_tx",
    }


def synth_normal_transactions(*, count: int, amount_range: tuple,
                                agent: str = "SYN-AGENT-1",
                                provider: str = "bkash",
                                seed: int = 5) -> list[dict]:
    rng = random.Random(seed)
    base = datetime.now(timezone.utc)
    return [
        {
            "id": transaction_id(),
            "timestamp": (base + timedelta(seconds=i * 30)).isoformat(),
            "amount": round(rng.uniform(*amount_range), 2),
            "direction": "cash_out",
            "provider": provider,
            "customer_id": _tid("CUST"),
            "agent_id": agent,
            "label": "synth_normal",
        }
        for i in range(count)
    ]


def synth_split_pattern(*, amount_each: float, count: int,
                          agent: str = "SYN-AGENT-1",
                          provider: str = "bkash") -> list[dict]:
    """Just-under-threshold structuring transaction pattern (Section 9)."""
    base = datetime.now(timezone.utc)
    return [
        {
            "id": transaction_id(),
            "timestamp": (base + timedelta(seconds=i * 90)).isoformat(),
            "amount": amount_each,
            "direction": "cash_out",
            "provider": provider,
            "customer_id": _tid("CUST"),
            "agent_id": agent,
            "label": "synth_split_injected",
            "is_anomaly": True,
        }
        for i in range(count)
    ]


def build_labeled_dataset(
    *,
    n_normal: int = 400,
    include_patterns: Sequence[str] = (
        "structuring",
        "near_identical_repeats",
        "velocity_spike",
        "circular",
    ),
    seed: int = 29,
) -> List[Dict[str, Any]]:
    """Combined labeled set for Layer 2 metrics.

    Each row carries ``is_anomaly`` (bool) and ``expected_pattern`` (str).
    """
    rng = random.Random(seed)
    rows: List[Dict[str, Any]] = []
    # Legitimate baseline
    rows.extend(salary_day_normal(n=n_normal // 2, seed=seed))
    rows.extend(eid_pre_holiday_normal(n=n_normal // 2, seed=seed + 1))

    # Injected anomalies
    if "structuring" in include_patterns:
        rows.extend(structuring_pattern(seed=seed + 2))
    if "near_identical_repeats" in include_patterns:
        rows.extend(near_identical_repeats(seed=seed + 3))
    if "velocity_spike" in include_patterns:
        rows.extend(velocity_spike(seed=seed + 4))
    if "circular" in include_patterns:
        rows.extend(circular_activity(seed=seed + 5))

    rng.shuffle(rows)
    return rows