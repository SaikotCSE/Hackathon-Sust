"""Bootstraps the prototype: creates one agent + three provider balances + a
handful of recent transactions + balance-history so the dashboard isn't empty
on first paint."""
from __future__ import annotations

import random
from datetime import datetime, timedelta
from typing import List

from sqlmodel import Session, select

from ..models.database import (
    Agent,
    BalanceHistory,
    ProviderBalance,
    Transaction,
    User,
)
from ..simulation.engine import BASELINE, PROVIDERS


def seed_if_empty(session: Session) -> Agent:
    agent = session.exec(select(Agent).limit(1)).first()
    if agent is not None:
        return agent

    agent = Agent(code="AGT-DHK-014", display_name="Demo Super Agent", area="Dhaka-Mirpur")
    session.add(agent)
    session.commit()
    session.refresh(agent)

    for prov in PROVIDERS:
        session.add(ProviderBalance(agent_id=agent.id, provider=prov, balance=BASELINE[prov]))
    session.add(ProviderBalance(agent_id=agent.id, provider="physical", balance=BASELINE["physical"]))
    session.commit()

    # Seed a few days of balance history with gentle daily pattern
    now = datetime.utcnow()
    rng = random.Random(7)
    for hours_ago in range(48, 0, -1):
        ts = now - timedelta(hours=hours_ago)
        for prov in PROVIDERS:
            base = BASELINE[prov]
            wobble = rng.uniform(-0.05, 0.05) * base
            session.add(BalanceHistory(
                agent_id=agent.id, provider=prov,
                balance=max(500.0, base + wobble),
                physical_cash=BASELINE["physical"],
                ts=ts,
            ))
    session.commit()

    # Seed some realistic recent transactions so anomaly rules have a chance to fire
    counterparties = [f"C{i:04d}" for i in range(1, 81)]
    for i in range(40):
        ts = now - timedelta(minutes=rng.randint(0, 25))
        provider = rng.choice(PROVIDERS)
        tx_type = rng.choices(["cash_in", "cash_out"], weights=[0.45, 0.55])[0]
        amount = float(rng.choice([
            rng.randint(200, 1500),
            rng.randint(1500, 4500),
            rng.randint(4500, 8500),
        ]))
        session.add(Transaction(
            agent_id=agent.id, provider=provider, tx_type=tx_type,
            amount=amount, counterparty_id=rng.choice(counterparties),
            area=agent.area, ts=ts,
        ))
    session.commit()

    # Seed a few demo users covering every RBAC role
    seed_users = [
        ("agent",     "agent",     "Multi-Provider Agent",         None),
        ("ops",       "ops",       "Provider Operations",          None),
        ("field",     "ops",       "Field Officer (Ops tier)",     None),
        ("manager",   "ops",       "Area Manager (Ops tier)",      None),
        ("risk",      "risk",      "Risk / Compliance Analyst",    None),
        ("provider_bkash",  "provider", "bKash Provider View",   "bkash"),
        ("provider_nagad",  "provider", "Nagad Provider View",   "nagad"),
        ("provider_rocket", "provider", "Rocket Provider View",  "rocket"),
        ("mgmt",      "management", "Management",                   None),
    ]
    for username, role, name, provider in seed_users:
        session.add(User(username=username, display_name=name, role=role, provider=provider, area="Dhaka"))
    session.commit()
    return agent