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
    Alert,
    AnomalyEvent,
    BalanceHistory,
    ProviderBalance,
    Transaction,
    User,
)
from ..simulation.engine import BASELINE, PROVIDERS, data_quality_for
from ..services.liquidity import compute_forecast


def seed_if_empty(session: Session) -> Agent:
    agent = session.exec(select(Agent).limit(1)).first()
    if agent is not None:
        return agent

    # ------------------------------------------------------------------
    # Seed MULTIPLE agents across multiple areas so the management rollup
    # has something real to aggregate over. The original seed created
    # exactly one agent in one area, which made the management view
    # functionally a single-agent dashboard in disguise.
    # ------------------------------------------------------------------
    agents_seed = [
        # (code, display_name, area, baseline_balances, history_depleted_providers)
        ("AGT-DHK-014", "Demo Super Agent",     "Dhaka-Mirpur",   BASELINE,                    []),
        ("AGT-DHK-022", "Gulshan Agent",        "Dhaka-Gulshan",  dict(BASELINE, bkash=0.0),   ["bkash"]),
        ("AGT-DHK-031", "Uttara Agent",         "Dhaka-Uttara",   dict(BASELINE, nagad=1_500.0, rocket=1_200.0, bkash=0.0), ["bkash", "nagad", "rocket"]),
        ("AGT-CTG-007", "Chittagong Agent",     "Chittagong",     BASELINE,                    []),
        ("AGT-CTG-013", "Patiya Sub-Agent",     "Chittagong-Patiya", dict(BASELINE, bkash=400.0, nagad=600.0, rocket=900.0), ["bkash", "nagad", "rocket"]),
    ]

    agents = []
    for code, name, area, balances, depleted in agents_seed:
        a = Agent(code=code, display_name=name, area=area)
        session.add(a)
        session.commit()
        session.refresh(a)
        agents.append((a, balances, depleted))

    # Per-agent provider balances + physical cash + history. We seed two
    # layers of history:
    #   (a) hourly samples for 48h — feeds longer-window anomaly patterns.
    #   (b) per-minute samples for the last 60 min — needed by rate_projection
    #       (Module 2) to compute a burn rate for every agent, not just the
    #       one the user has been ticking. Without this the management
    #       rollup would show "no forecast yet" everywhere.
    now = datetime.utcnow()
    for idx, (a, balances, _depleted) in enumerate(agents):
        for prov in PROVIDERS:
            session.add(ProviderBalance(
                agent_id=a.id, provider=prov, balance=float(balances.get(prov, BASELINE[prov])),
            ))
        session.add(ProviderBalance(
            agent_id=a.id, provider="physical", balance=float(balances.get("physical", BASELINE["physical"])),
        ))

        rng = random.Random(7 + idx * 31)
        # (a) hourly samples for the last 48h
        for hours_ago in range(48, 0, -1):
            ts = now - timedelta(hours=hours_ago)
            for prov in PROVIDERS:
                base = float(balances.get(prov, BASELINE[prov]))
                wobble = rng.uniform(-0.05, 0.05) * base
                session.add(BalanceHistory(
                    agent_id=a.id, provider=prov,
                    balance=max(0.0, base + wobble),
                    physical_cash=float(balances.get("physical", BASELINE["physical"])),
                    ts=ts,
                ))

        # (b) per-minute samples for the last 60 minutes. Trend the balance
        # toward its current value (or zero for "depleted" providers) so
        # the burn rate the forecast reads is plausible. Includes physical
        # cash so the dashboard's "physical: no forecast yet" reason stops
        # showing up for every agent.
        physical_baseline = float(balances.get("physical", BASELINE["physical"]))
        for minutes_ago in range(60, 0, -1):
            ts = now - timedelta(minutes=minutes_ago)
            for prov in PROVIDERS:
                target = float(balances.get(prov, BASELINE[prov]))
                # small fractional draw each minute so rate is non-zero
                if target > 0:
                    burn_step = rng.uniform(0.001, 0.006) * target
                    sample = max(0.0, target + burn_step * (minutes_ago / 60.0))
                else:
                    sample = 0.0
                jitter = rng.uniform(-0.005, 0.005) * max(1.0, sample)
                session.add(BalanceHistory(
                    agent_id=a.id, provider=prov,
                    balance=max(0.0, sample + jitter),
                    physical_cash=physical_baseline,
                    ts=ts,
                ))
            # physical cash — gentle 0.1% drift per minute so its forecast
            # also has a non-zero burn-rate signal.
            physical_sample = max(
                0.0,
                physical_baseline * (1.0 + rng.uniform(-0.001, 0.001) * (60 - minutes_ago) / 60.0),
            )
            session.add(BalanceHistory(
                agent_id=a.id, provider="physical",
                balance=physical_sample,
                physical_cash=physical_sample,
                ts=ts,
            ))
        session.commit()

    # Per-agent recent transactions + a couple of older ones so the
    # "recurring problems over time" rollup has data spanning multiple days.
    for idx, (a, _balances, _depleted) in enumerate(agents):
        rng = random.Random(11 + idx * 17)
        counterparties = [f"C{i:04d}" for i in range(1, 81)]
        # recent (last 25 min)
        for i in range(30):
            ts = now - timedelta(minutes=rng.randint(0, 25))
            provider = rng.choice(PROVIDERS)
            tx_type = rng.choices(["cash_in", "cash_out"], weights=[0.45, 0.55])[0]
            amount = float(rng.choice([
                rng.randint(200, 1500),
                rng.randint(1500, 4500),
                rng.randint(4500, 8500),
            ]))
            session.add(Transaction(
                agent_id=a.id, provider=provider, tx_type=tx_type,
                amount=amount, counterparty_id=rng.choice(counterparties),
                area=a.area, ts=ts,
            ))
        # historical transactions spread across the last 7 days so the
        # recurring-problems rollup actually has multiple time windows
        for day_ago in range(1, 7):
            for i in range(6):
                ts = now - timedelta(days=day_ago, minutes=rng.randint(0, 1200))
                provider = rng.choice(PROVIDERS)
                tx_type = rng.choices(["cash_in", "cash_out"], weights=[0.45, 0.55])[0]
                amount = float(rng.choice([
                    rng.randint(200, 1500),
                    rng.randint(1500, 4500),
                    rng.randint(4500, 8500),
                ]))
                session.add(Transaction(
                    agent_id=a.id, provider=provider, tx_type=tx_type,
                    amount=amount, counterparty_id=rng.choice(counterparties),
                    area=a.area, ts=ts,
                ))
        session.commit()

    # Warm-up: compute a ForecastSnapshot for every (agent, provider). The
    # management rollup reads from this table — without warm-up every agent
    # would show "no forecast yet" until the user manually ticks each one.
    for idx, (a, _balances, _depleted) in enumerate(agents):
        for prov in list(PROVIDERS) + ["physical"]:
            try:
                dq = float(data_quality_for(session, prov))
                compute_forecast(session, a.id, prov, data_quality=dq)
            except Exception:
                # If lightgbm is missing or any single forecast fails we
                # don't want to abort the whole seed — the rate_projection
                # path still produces something useful.
                pass

    # Seed some alerts per agent — both recent (<24h) and older (1-6d) —
    # so the management rollup's "recurring_problems.by_agent" can show a
    # 24h vs 7d comparison rather than coming back empty.
    rng_al = random.Random(31)
    for idx, (a, _balances, depleted) in enumerate(agents):
        for day_ago in range(0, 7):
            n_alerts = rng_al.randint(1, 3) if day_ago == 0 else rng_al.randint(0, 2)
            for _ in range(n_alerts):
                # bias toward depleted providers so the data tells a story
                if depleted and rng_al.random() < 0.75:
                    prov = rng_al.choice(depleted)
                else:
                    prov = rng_al.choice(list(PROVIDERS))
                sev = rng_al.choices(
                    ["low", "high", "critical"], weights=[0.55, 0.30, 0.15]
                )[0]
                ts = (
                    now - timedelta(minutes=rng_al.randint(5, 60 * 22))
                    if day_ago == 0
                    else now - timedelta(days=day_ago, minutes=rng_al.randint(0, 1200))
                )
                session.add(Alert(
                    agent_id=a.id,
                    provider=prov,
                    severity=sev,
                    priority_score={"low": 45, "high": 70, "critical": 92}[sev],
                    title=f"{prov.upper()} {sev} risk",
                    summary=f"Seeded {sev} alert for {prov}",
                    reasons_json="[]",
                    evidence_json="[]",
                    confidence=0.7,
                    recommended_actions_json="[]",
                    fused_explanation="Seeded for management rollup demo.",
                    owner_role="ops",
                    owner_label="Provider Operations",
                    initial_owner="liquidity",
                    status="open",
                    created_at=ts,
                    updated_at=ts,
                ))
        session.commit()

    # Seed a handful of anomaly events per agent so the management view's
    # recurring-problems rollup has data points to aggregate. We seed
    # both recent (<24h) and historical (1-6d) events so the "last 24h vs
    # last 7 days" comparison has something to show.
    rng_an = random.Random(23)
    for idx, (a, _balances, depleted) in enumerate(agents):
        rules_pool = ["repeated_amount", "burst_count", "unusual_hour", "counterparty_fanin"]
        for day_ago in range(0, 7):
            if day_ago == 0:
                # 2-3 events in the last 24h
                n_events = rng_an.randint(2, 3)
                for _ in range(n_events):
                    ts = now - timedelta(minutes=rng_an.randint(5, 60 * 22))
                    rule = rng_an.choice(rules_pool)
                    prov = rng_an.choice(depleted) if depleted else rng_an.choice(list(PROVIDERS))
                    session.add(AnomalyEvent(
                        agent_id=a.id, provider=prov, rule=rule,
                        score=float(rng_an.uniform(0.55, 0.92)),
                        confidence=float(rng_an.uniform(0.45, 0.85)),
                        summary=f"{rule} flagged for {prov}",
                        evidence_json="{}",
                        ts=ts,
                    ))
            else:
                # 1-2 events on each of the last 6 days
                n_events = rng_an.randint(0, 2)
                for _ in range(n_events):
                    ts = now - timedelta(days=day_ago, minutes=rng_an.randint(0, 1200))
                    rule = rng_an.choice(rules_pool)
                    prov = rng_an.choice(depleted) if depleted else rng_an.choice(list(PROVIDERS))
                    session.add(AnomalyEvent(
                        agent_id=a.id, provider=prov, rule=rule,
                        score=float(rng_an.uniform(0.45, 0.85)),
                        confidence=float(rng_an.uniform(0.35, 0.80)),
                        summary=f"{rule} flagged for {prov}",
                        evidence_json="{}",
                        ts=ts,
                    ))
        session.commit()

    # Seed a few demo users covering every RBAC role. The original
    # single-agent seed kept these in this same function; we keep them
    # here so the seed contract is unchanged.
    seed_users = [
        ("agent",     "agent",     "Multi-Provider Agent",         None),
        ("ops",       "ops",       "Provider Operations",          None),
        ("field",     "ops",       "Field Officer (Ops tier)",     None),
        ("risk",      "risk",      "Risk analyst",                 None),
        ("provider_bkash",  "provider", "bKash Provider View",   "bkash"),
        ("provider_nagad",  "provider", "Nagad Provider View",   "nagad"),
        ("provider_rocket", "provider", "Rocket Provider View",  "rocket"),
        ("mgmt",      "management", "Management",                   None),
    ]
    for username, role, name, provider in seed_users:
        # Idempotent — only insert if a row with this username doesn't
        # already exist. This protects the user roster from accumulating
        # duplicate rows across reboots of the prototype (and from stale
        # rows like a legacy "Area Manager (Ops tier)" entry that was
        # never declared in this seed list in the first place).
        exists = session.exec(select(User).where(User.username == username)).first()
        if exists is None:
            session.add(User(username=username, display_name=name, role=role, provider=provider, area="Dhaka"))
    session.commit()

    # Return the demo super agent (kept for back-compat with the original
    # seed contract: callers expect a single Agent back).
    return agents[0][0] if agents else None
    return agent