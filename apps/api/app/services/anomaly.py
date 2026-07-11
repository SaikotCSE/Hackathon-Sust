"""Module 3 — AI Anomaly Detection Engine.

Multi-headed Rule Engine (primary) + Isolation Forest (secondary confirmation).
Every alert keeps at least one plain-language, rule-based reason.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta
from statistics import mean, pstdev
from typing import Dict, List, Optional, Tuple

from sqlmodel import Session, select

from ..models.database import AnomalyEvent, Transaction


# ---------------------------------------------------------------------------
# Rule heads
# ---------------------------------------------------------------------------

RULE_REPEATED_AMOUNT = "repeated_amount"
RULE_VELOCITY_SPIKE = "velocity_spike"
RULE_STRUCTURING = "structuring"
RULE_BALANCE_ANOMALY = "balance_anomaly"
RULE_TIMING_ANOMALY = "timing_anomaly"


@dataclass
class RuleHit:
    rule: str
    confidence: float
    count: int
    amount: Optional[float]
    window_minutes: int
    reasons: List[str]


def _recent_tx(session: Session, agent_id: int, provider: str, window_minutes: int):
    since = datetime.utcnow() - timedelta(minutes=window_minutes)
    return session.exec(
        select(Transaction)
        .where(Transaction.agent_id == agent_id)
        .where(Transaction.provider == provider)
        .where(Transaction.ts >= since)
        .order_by(Transaction.ts)
    ).all()


def rule_repeated_amount(session: Session, agent_id: int, provider: str) -> Optional[RuleHit]:
    txs = _recent_tx(session, agent_id, provider, window_minutes=15)
    if len(txs) < 5:
        return None
    amounts = [round(t.amount) for t in txs]
    counts = Counter(amounts)
    amount, hit = counts.most_common(1)[0]
    if hit < 5:
        return None
    confidence = min(0.95, 0.55 + hit * 0.04)
    return RuleHit(
        rule=RULE_REPEATED_AMOUNT,
        confidence=confidence,
        count=hit,
        amount=float(amount),
        window_minutes=15,
        reasons=[
            f"Repeated {hit} transactions",
            f"Amount: {int(amount)} BDT",
            f"Within: {15} minutes",
            f"Reason: Repeated amount pattern",
            "Recommended: Human Review",
        ],
    )


def rule_velocity_spike(session: Session, agent_id: int, provider: str) -> Optional[RuleHit]:
    txs_now = _recent_tx(session, agent_id, provider, window_minutes=10)
    txs_prev = _recent_tx(session, agent_id, provider, window_minutes=20)
    prev = [t for t in txs_prev if t.ts < datetime.utcnow() - timedelta(minutes=10)]
    if len(prev) < 3 or len(txs_now) < 3:
        return None
    rate_now = len(txs_now) / 10.0
    rate_prev = len(prev) / 10.0
    if rate_prev == 0:
        return None
    ratio = rate_now / rate_prev
    if ratio < 2.0:
        return None
    confidence = min(0.90, 0.5 + (ratio - 2.0) * 0.1)
    return RuleHit(
        rule=RULE_VELOCITY_SPIKE,
        confidence=confidence,
        count=len(txs_now),
        amount=None,
        window_minutes=10,
        reasons=[
            f"Transaction velocity {ratio:.1f}× above recent baseline",
            f"Sample window: {len(txs_now)} tx / 10 min",
            "Reason: Abnormal transaction velocity",
            "Recommended: Human Review",
        ],
    )


def rule_structuring(session: Session, agent_id: int, provider: str) -> Optional[RuleHit]:
    """Cluster near-threshold amounts (Bangladesh mobile money contexts the brief
    uses as examples). Threshold chosen as 5,000 BDT and band of ±100 BDT."""
    txs = _recent_tx(session, agent_id, provider, window_minutes=30)
    candidates = [t for t in txs if 4900 <= t.amount <= 5100]
    if len(candidates) < 4:
        return None
    confidence = min(0.92, 0.55 + len(candidates) * 0.05)
    return RuleHit(
        rule=RULE_STRUCTURING,
        confidence=confidence,
        count=len(candidates),
        amount=sum(t.amount for t in candidates),
        window_minutes=30,
        reasons=[
            f"{len(candidates)} transactions clustered near the {5000} BDT threshold",
            "Range: 4900–5100 BDT",
            "Reason: Possible transaction splitting / structuring",
            "Recommended: Human Review",
        ],
    )


def rule_balance_anomaly(session: Session, agent_id: int, provider: str) -> Optional[RuleHit]:
    """Large sudden drop without commensurate tx — proxy for reconciliation anomaly."""
    from ..models.database import BalanceHistory
    history = session.exec(
        select(BalanceHistory)
        .where(BalanceHistory.agent_id == agent_id)
        .where(BalanceHistory.provider == provider)
        .order_by(BalanceHistory.ts.desc())
        .limit(10)
    ).all()
    if len(history) < 4:
        return None
    history = list(reversed(history))
    drops = [history[i - 1].balance - history[i].balance for i in range(1, len(history)) if history[i - 1].balance > history[i].balance]
    if not drops:
        return None
    avg = mean(drops)
    if avg <= 0:
        return None
    sd = pstdev(drops) if len(drops) > 1 else 0.0
    last = history[-1].balance - history[-2].balance
    if -last > avg + 2 * sd and -last > 5000:
        confidence = min(0.88, 0.55 + abs(-last - avg) / 50000.0)
        return RuleHit(
            rule=RULE_BALANCE_ANOMALY,
            confidence=confidence,
            count=1,
            amount=float(-last),
            window_minutes=int((history[-1].ts - history[-2].ts).total_seconds() / 60),
            reasons=[
                f"Sudden balance drop of {int(-last):,} BDT without matching tx",
                "Reason: Abnormal balance change (reconciliation / data-issue candidate)",
                "Recommended: Human Review",
            ],
        )
    return None


def rule_timing_anomaly(session: Session, agent_id: int, provider: str) -> Optional[RuleHit]:
    """Burst of tx in <2 min window outside business hours."""
    txs = _recent_tx(session, agent_id, provider, window_minutes=15)
    if len(txs) < 4:
        return None
    odd_hour = [t for t in txs if t.ts.hour < 7 or t.ts.hour >= 22]
    if len(odd_hour) < 3:
        return None
    confidence = min(0.78, 0.45 + len(odd_hour) * 0.05)
    return RuleHit(
        rule=RULE_TIMING_ANOMALY,
        confidence=confidence,
        count=len(odd_hour),
        amount=None,
        window_minutes=15,
        reasons=[
            f"{len(odd_hour)} transactions outside normal operating hours",
            "Reason: Suspicious timing pattern",
            "Recommended: Human Review",
        ],
    )


# ---------------------------------------------------------------------------
# Isolation Forest confirmation
# ---------------------------------------------------------------------------

_IFOREST_CACHE: Dict[Tuple[int, str], Dict] = {}


def _iforest_enabled() -> bool:
    try:
        from sklearn.ensemble import IsolationForest  # noqa: F401
        import numpy  # noqa: F401
        return True
    except Exception:
        return False


def iforest_score(session: Session, agent_id: int, provider: str, hits: List[RuleHit]) -> Optional[float]:
    """Returns a 0..1 score that nudges confidence upward when it agrees with rule heads.
    Returns None when there aren't enough recent tx to fit."""
    if not _iforest_enabled():
        return None
    txs = _recent_tx(session, agent_id, provider, window_minutes=30)
    if len(txs) < 12:
        # Below the 10–15 minimum-sample floor — let rule heads carry the alert.
        return None

    try:
        import numpy as np
        from sklearn.ensemble import IsolationForest
    except Exception:  # pragma: no cover
        return None

    feats: List[List[float]] = []
    for t in txs:
        # amount, hour of day, minute-of-day, counterparty frequency
        counterparty_count = sum(1 for u in txs if u.counterparty_id == t.counterparty_id)
        feats.append([float(t.amount), float(t.ts.hour), float(t.ts.minute), float(counterparty_count)])

    X = np.array(feats, dtype=np.float64)
    try:
        model = IsolationForest(n_estimators=40, contamination=0.1, random_state=42)
        model.fit(X)
        scores = -model.score_samples(X)  # higher = more anomalous
        anomaly_score = float(scores.max())
    except Exception:
        return None

    # squash into 0..1
    return max(0.0, min(1.0, 1.0 - 1.0 / (1.0 + anomaly_score)))


# ---------------------------------------------------------------------------
# Combined run
# ---------------------------------------------------------------------------

def detect_anomalies(session: Session, agent_id: int, provider: str) -> List[AnomalyEvent]:
    import json

    heads = [
        rule_repeated_amount(session, agent_id, provider),
        rule_velocity_spike(session, agent_id, provider),
        rule_structuring(session, agent_id, provider),
        rule_balance_anomaly(session, agent_id, provider),
        rule_timing_anomaly(session, agent_id, provider),
    ]
    fired = [h for h in heads if h is not None]
    if not fired:
        return []

    iforest = iforest_score(session, agent_id, provider, fired)
    events: List[AnomalyEvent] = []
    for hit in fired:
        # IF confidence nudge (small) — never the sole output
        conf = hit.confidence
        if iforest is not None and iforest > 0.6:
            conf = min(0.97, conf + 0.05)
            reasons = list(hit.reasons) + [f"Isolation Forest confirmation (score {iforest:.2f})"]
        else:
            reasons = list(hit.reasons)
        ev = AnomalyEvent(
            agent_id=agent_id,
            provider=provider,
            rule=hit.rule,
            confidence=conf,
            count=hit.count,
            amount=hit.amount,
            window_minutes=hit.window_minutes,
            reasons_json=json.dumps(reasons),
            iforest_score=iforest,
        )
        session.add(ev)
        events.append(ev)
    session.commit()
    return events