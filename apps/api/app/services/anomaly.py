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

from ..models.database import AnomalyEvent, OperationalContextEvent, Transaction


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


def _tx_evidence(txs: List[Transaction], limit: int = 8) -> str:
    """Compact, auditable identifiers for the exact records behind a rule hit."""
    shown = txs[-limit:]
    return "Evidence transactions: " + "; ".join(
        f"tx#{t.id} {t.ts.isoformat()} {t.counterparty_id} {t.tx_type} {t.amount:.0f} BDT"
        for t in shown
    )


def active_operational_context(
    session: Session, agent_id: int, provider: str, *, now: Optional[datetime] = None
) -> Optional[OperationalContextEvent]:
    """Return observable context without consulting evaluation labels."""
    now = now or datetime.utcnow()
    return session.exec(
        select(OperationalContextEvent)
        .where(OperationalContextEvent.agent_id == agent_id)
        .where(OperationalContextEvent.provider == provider)
        .where(OperationalContextEvent.started_at <= now)
        .where(OperationalContextEvent.ends_at >= now)
        .order_by(OperationalContextEvent.started_at.desc())
        .limit(1)
    ).first()


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
    matched = [t for t in txs if round(t.amount) == amount]
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
            "Pattern: near-identical repeated amount",
            _tx_evidence(matched),
            "Uncertainty: repeated values can arise from legitimate fixed-price services; requires human review",
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
            "Pattern: unusual transaction velocity",
            _tx_evidence(txs_now),
            "Uncertainty: festivals, salary days, or campaigns can create benign spikes; requires human review",
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
            "Pattern: amounts consistent with possible transaction splitting; intent is unknown",
            _tx_evidence(candidates),
            "Uncertainty: common bill values can cluster naturally; requires human review",
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
    previous, current = history[-2], history[-1]
    last_drop = previous.balance - current.balance
    matching = session.exec(
        select(Transaction)
        .where(Transaction.agent_id == agent_id)
        .where(Transaction.provider == provider)
        .where(Transaction.ts > previous.ts)
        .where(Transaction.ts <= current.ts)
        .where(Transaction.status == "success")
    ).all()
    # Provider e-money moves opposite to physical cash from the agent's view:
    # cash-in consumes e-money, while cash-out replenishes it.
    explained_drop = sum(t.amount if t.tx_type == "cash_in" else -t.amount for t in matching)
    unexplained_drop = last_drop - explained_drop
    if last_drop > avg + 2 * sd and unexplained_drop > 5000:
        confidence = min(0.88, 0.55 + abs(unexplained_drop) / 50000.0)
        return RuleHit(
            rule=RULE_BALANCE_ANOMALY,
            confidence=confidence,
            count=1,
            amount=float(unexplained_drop),
            window_minutes=int((history[-1].ts - history[-2].ts).total_seconds() / 60),
            reasons=[
                f"Balance fell {last_drop:,.0f} BDT; successful transactions explain {explained_drop:,.0f} BDT",
                f"Unreconciled difference: {unexplained_drop:,.0f} BDT",
                f"Evidence snapshots: history#{history[-2].id} → history#{history[-1].id}",
                "Pattern: unusual balance change (reconciliation or data-quality candidate)",
                "Uncertainty: provider delays or manual adjustments can explain this; requires human review",
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
            "Pattern: unusual timing pattern",
            _tx_evidence(odd_hour),
            "Uncertainty: extended opening hours may be legitimate; requires human review",
        ],
    )


# ---------------------------------------------------------------------------
# Isolation Forest confirmation
# ---------------------------------------------------------------------------

_IFOREST_CACHE: Dict[Tuple[int, str], Dict] = {}
_IFOREST_CACHE_TTL_SECONDS = 60


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

    # Cache key includes a coarse fingerprint of the input window so that a
    # burst of fresh transactions invalidates the cached fit, but a stable
    # 30-minute window can reuse it.
    _fingerprint = (len(txs), int(txs[-1].ts.timestamp()) // 60)
    cache_key = (agent_id, provider)
    now = datetime.utcnow()
    cached = _IFOREST_CACHE.get(cache_key)
    if (
        cached is not None
        and cached.get("fingerprint") == _fingerprint
        and (now - cached["trained_at"]).total_seconds() < _IFOREST_CACHE_TTL_SECONDS
    ):
        return cached["score"]

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

    score = max(0.0, min(1.0, 1.0 - 1.0 / (1.0 + anomaly_score)))
    _IFOREST_CACHE[cache_key] = {
        "fingerprint": _fingerprint,
        "trained_at": now,
        "score": score,
    }
    return score


# ---------------------------------------------------------------------------
# Combined run
# ---------------------------------------------------------------------------

def detect_anomalies(session: Session, agent_id: int, provider: str) -> List[AnomalyEvent]:
    import json

    operational_context = active_operational_context(session, agent_id, provider)
    expected_volume_context = (
        operational_context is not None
        and operational_context.kind in {"salary_day", "demand_surge", "campaign", "local_event"}
    )
    heads = [
        rule_repeated_amount(session, agent_id, provider),
        None if expected_volume_context else rule_velocity_spike(session, agent_id, provider),
        rule_structuring(session, agent_id, provider),
        rule_balance_anomaly(session, agent_id, provider),
        None if expected_volume_context else rule_timing_anomaly(session, agent_id, provider),
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
    matched = [t for t in txs if round(t.amount) == amount]
