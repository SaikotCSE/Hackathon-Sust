"""Synthetic data simulation engine — Module 5 of the brief (Data Simulation).

Builds a realistic, *configurable* transaction stream per provider and exposes
scenario injectors that the demo's debug panel calls. Ground-truth labels are
written so Module 9 can compute anomaly precision/recall honestly.
"""
from __future__ import annotations

import json
import random
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Dict, List, Optional

from sqlmodel import Session, select

from ..models.database import (
    Agent,
    AnomalyEvent,
    BalanceHistory,
    DataQualityEvent,
    OperationalContextEvent,
    ProviderBalance,
    ScenarioEvent,
    Transaction,
)


PROVIDERS = ("bkash", "nagad", "rocket")

# Baseline balances from problem statement (sum to ৳225,000)
BASELINE = {
    "physical": 100_000.0,
    "bkash":     5_000.0,
    "nagad":    80_000.0,
    "rocket":   40_000.0,
}


@dataclass
class ScenarioSpec:
    kind: str
    label: str
    provider: Optional[str] = None
    intended_severity: str = "normal"
    is_anomaly_ground_truth: bool = False
    duration_minutes: int = 5
    note: str = ""


class SimulationEngine:
    """Single-process simulation tick. Holds per-provider state in memory for
    speed but persists every tx / balance snapshot so reloads are warm."""

    def __init__(self, session: Session, agent_id: int) -> None:
        self.session = session
        self.agent_id = agent_id
        self.active_scenarios: List[Dict] = []
        self.injectors: Dict[str, callable] = {
            "bkash_surge": self._inject_bkash_surge,
            "repeated_amount": self._inject_repeated_amount,
            "structuring": self._inject_structuring,
            "rocket_delay": self._inject_rocket_delay,
            "salary_day": self._inject_salary_day,
        }
        # In-memory counterparty pool for this agent
        self.counterparties = [f"C{i:04d}" for i in range(1, 41)]
        random.seed(42 + agent_id)
        # Scenario injection and ticking are separate HTTP requests and therefore
        # separate engine instances. Restore still-active scenarios from the DB;
        # keeping them only in memory made the injector UI a no-op.
        now = datetime.utcnow()
        recent = self.session.exec(
            select(ScenarioEvent)
            .where(ScenarioEvent.agent_id == agent_id)
            .order_by(ScenarioEvent.injected_at.desc())
            .limit(50)
        ).all()
        for ev in recent:
            duration = max(1, int(getattr(ev, "duration_minutes", 5) or 5))
            if (now - ev.injected_at).total_seconds() < duration * 60:
                self.active_scenarios.append({
                    "id": ev.id, "kind": ev.kind, "provider": ev.provider,
                    "started_at": ev.injected_at, "duration_minutes": duration,
                    "intent": ev.intended_severity,
                })

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def inject_scenario(self, spec: ScenarioSpec) -> ScenarioEvent:
        injector = self.injectors.get(spec.kind)
        if injector is None:
            raise ValueError(f"Unknown scenario: {spec.kind}")
        provider = spec.provider or ("bkash" if spec.kind == "bkash_surge" else "rocket" if spec.kind == "rocket_delay" else "bkash")

        ev = ScenarioEvent(
            agent_id=self.agent_id,
            provider=provider,
            kind=spec.kind,
            intended_severity=spec.intended_severity,
            is_anomaly_ground_truth=spec.is_anomaly_ground_truth,
            note=spec.note or spec.label,
            duration_minutes=spec.duration_minutes,
        )
        self.session.add(ev)
        self.session.commit()
        self.session.refresh(ev)

        self.active_scenarios.append({
            "id": ev.id,
            "kind": spec.kind,
            "provider": provider,
            "started_at": datetime.utcnow(),
            "duration_minutes": spec.duration_minutes,
            "intent": spec.intended_severity,
        })
        # Persist observed operational context separately from evaluation
        # ground truth. The detector may consume this row, but never reads the
        # ScenarioEvent label or intended severity.
        context_kind = {
            "salary_day": "salary_day",
            "bkash_surge": "demand_surge",
        }.get(spec.kind)
        if context_kind:
            self.session.add(OperationalContextEvent(
                agent_id=self.agent_id,
                provider=provider,
                kind=context_kind,
                note=(
                    "Known salary-day service demand from the operations calendar"
                    if context_kind == "salary_day"
                    else "Known provider demand surge reported by Operations"
                ),
                source="simulated-operations-calendar",
                started_at=ev.injected_at,
                ends_at=ev.injected_at + timedelta(minutes=spec.duration_minutes),
            ))
            self.session.commit()
        injector(provider=provider)
        return ev

    def tick(self, *, n_transactions: int = 6) -> List[Transaction]:
        """One simulation tick: produce n_transactions new tx and update balances."""
        agent = self.session.get(Agent, self.agent_id)
        if agent is None:
            return []

        # Refresh in-memory views of provider balances
        balances = {pb.provider: pb.balance for pb in self.session.exec(
            select(ProviderBalance).where(ProviderBalance.agent_id == self.agent_id)
        ).all()}
        for prov in PROVIDERS:
            balances.setdefault(prov, BASELINE[prov])

        # Expire scenarios
        now = datetime.utcnow()
        self.active_scenarios = [s for s in self.active_scenarios if (now - s["started_at"]).total_seconds() < s["duration_minutes"] * 60]

        txs: List[Transaction] = []
        for _ in range(n_transactions):
            provider, tx_type, amount, counterparty = self._draw_tx(balances)
            area = agent.area
            tx = Transaction(
                agent_id=self.agent_id,
                provider=provider,
                tx_type=tx_type,
                amount=amount,
                counterparty_id=counterparty,
                area=area,
                ts=now,
            )
            self.session.add(tx)
            txs.append(tx)
            # Agent-side accounting keeps provider e-money and physical cash
            # separate and moving in opposite directions:
            #   cash-out: customer sends e-money to agent; agent gives cash
            #   cash-in:  customer gives cash; agent sends e-money
            delta = amount if tx_type == "cash_out" else -amount
            balances[provider] = max(0.0, balances.get(provider, 0.0) + delta)
            pb = self.session.exec(
                select(ProviderBalance).where(ProviderBalance.agent_id == self.agent_id).where(ProviderBalance.provider == provider)
            ).first()
            if pb is None:
                pb = ProviderBalance(agent_id=self.agent_id, provider=provider, balance=balances[provider])
                self.session.add(pb)
            else:
                pb.balance = balances[provider]
                pb.updated_at = now

        # Drain physical cash for cash_out, replenish for cash_in
        physical = self.session.exec(
            select(ProviderBalance).where(ProviderBalance.agent_id == self.agent_id).where(ProviderBalance.provider == "physical")
        ).first()
        # A customer cash-out consumes notes from the shared drawer; cash-in
        # replenishes it. This is deliberately tracked independently of each
        # provider wallet so a healthy aggregate cannot hide a cash shortage.
        cash_delta = sum(
            -tx.amount if tx.tx_type == "cash_out" else tx.amount
            for tx in txs
        )
        if physical is not None:
            physical.balance = max(0.0, physical.balance + cash_delta)
            physical.updated_at = now
        # Snapshot history for sparklines and forecast
        for provider in PROVIDERS:
            self.session.add(BalanceHistory(
                agent_id=self.agent_id, provider=provider, balance=balances.get(provider, 0.0),
                physical_cash=physical.balance if physical else BASELINE["physical"],
                ts=now,
            ))
        if physical is not None:
            self.session.add(BalanceHistory(
                agent_id=self.agent_id, provider="physical", balance=physical.balance,
                physical_cash=physical.balance, ts=now,
            ))

        self.session.commit()
        return txs

    # ------------------------------------------------------------------
    # Tx generator
    # ------------------------------------------------------------------

    def _draw_tx(self, balances: Dict[str, float]) -> tuple:
        active = self.active_scenarios
        current = max(active, key=lambda s: s["started_at"]) if active else None
        if current and current["kind"] == "bkash_surge" and current["provider"] == "bkash":
            # Sustained cash-in demand consumes the outlet's bKash e-money
            # position while replenishing physical cash.
            return ("bkash", "cash_in", random.randint(4000, 6500), random.choice(self.counterparties))
        if current and current["kind"] == "repeated_amount":
            return (current["provider"],
                    random.choice(["cash_in", "cash_out"]),
                    2375, random.choice(self.counterparties))
        if current and current["kind"] == "structuring":
            return (current["provider"], "cash_out", random.choice([4950, 4970, 5000, 5030, 5050]),
                    random.choice(self.counterparties))
        if current and current["kind"] == "salary_day":
            # Legitimate high-volume context: deliberately diverse amounts and
            # counterparties, avoiding the near-5,000 test band.
            return (current["provider"],
                    random.choices(["cash_in", "cash_out"], weights=[0.65, 0.35])[0],
                    float(random.choice([700, 1100, 1750, 2400, 3200, 6800, 7600, 9200])),
                    random.choice(self.counterparties))
        # Default normal flow — roughly proportional to provider health.
        # Clamp to a tiny positive floor so all-zero balances (every
        # provider depleted) don't crash random.choices with
        # "Total of weights must be greater than zero". Once a balance
        # is recovered a tick or two later, normal weighted sampling
        # resumes.
        weights = [max(float(balances.get(p, 0.0) or 0.0), 0.01) for p in PROVIDERS]
        provider = random.choices(list(PROVIDERS), weights=weights)[0]  # fmt:skip
        tx_type = random.choices(["cash_in", "cash_out"], weights=[0.45, 0.55])[0]
        amount = random.choice([
            random.randint(200, 1500),
            random.randint(1500, 4500),
            random.randint(4500, 8500),
        ])
        return provider, tx_type, float(amount), random.choice(self.counterparties)

    # ------------------------------------------------------------------
    # Scenario injectors (one-time impulse)
    # ------------------------------------------------------------------

    def _inject_bkash_surge(self, provider: str) -> None:
        # The stream itself becomes surge-heavy for the next duration_minutes minutes.
        # The injector itself just primes the state.
        pass

    def _inject_repeated_amount(self, provider: str) -> None:
        pass

    def _inject_structuring(self, provider: str) -> None:
        pass

    def _inject_rocket_delay(self, provider: str) -> None:
        # Represent a feed that is already late when the scenario is selected.
        # Backdating makes the demo deterministic: the very next tick must
        # enter safe fallback instead of waiting two real-time minutes.
        self.session.add(DataQualityEvent(
            provider="rocket", issue="delay",
            note="Injected via scenario panel — Rocket API delays",
            started_at=datetime.utcnow() - timedelta(minutes=3),
        ))
        self.session.commit()

    def _inject_salary_day(self, provider: str) -> None:
        # Salary day: legitimate spike in cash-out demand — should NOT be flagged anomalous.
        pass


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def data_quality_for(session: Session, provider: str) -> float:
    """Return a 0..1 health score for the provider feed. 1.0 = healthy.
    Active delay/outage events drag the score down."""
    now = datetime.utcnow()
    open_events = session.exec(
        select(DataQualityEvent).where(DataQualityEvent.provider == provider).where(DataQualityEvent.resolved_at == None)  # noqa: E711
    ).all()
    score = 1.0
    for ev in open_events:
        elapsed = (now - ev.started_at).total_seconds()
        age_factor = min(1.0, elapsed / 120.0)  # ramps up over 2 minutes
        if ev.issue == "outage":
            score -= 0.9 * age_factor
        elif ev.issue == "delay":
            # A fully late feed must cross the <0.5 safe-fallback threshold.
            score -= 0.75 * age_factor
        else:
            score -= 0.4 * age_factor
    return max(0.0, min(1.0, score))


def resolve_open_data_quality(session: Session, provider: str) -> int:
    open_events = session.exec(
        select(DataQualityEvent).where(DataQualityEvent.provider == provider).where(DataQualityEvent.resolved_at == None)  # noqa: E711
    ).all()
    n = 0
    for ev in open_events:
        ev.resolved_at = datetime.utcnow()
        session.add(ev)
        n += 1
    session.commit()
    return n
