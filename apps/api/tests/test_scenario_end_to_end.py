import json
import unittest
from datetime import datetime, timedelta

from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.models.database import (
    Agent,
    Alert,
    AnomalyEvent,
    BalanceHistory,
    ForecastSnapshot,
    ProviderBalance,
)
from app.routers.scenarios import inject as inject_scenario
from app.routers.scenarios import tick as run_cycle
from app.services.auth import Principal


class ScenarioEndToEndTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        SQLModel.metadata.create_all(self.engine)
        self.session = Session(self.engine)
        agent = Agent(code="E2E", display_name="Scenario E2E", area="Dhaka")
        self.session.add(agent)
        self.session.commit()
        self.session.refresh(agent)
        self.agent_id = agent.id
        balances = {"physical": 100_000.0, "bkash": 5_000.0, "nagad": 80_000.0, "rocket": 40_000.0}
        now = datetime.utcnow()
        for provider, balance in balances.items():
            self.session.add(ProviderBalance(agent_id=agent.id, provider=provider, balance=balance))
            for minute in range(8, 0, -1):
                self.session.add(BalanceHistory(
                    agent_id=agent.id,
                    provider=provider,
                    balance=balance + minute * 10,
                    physical_cash=balances["physical"],
                    ts=now - timedelta(minutes=minute),
                ))
        self.session.commit()
        self.principal = Principal("agent", "Agent", "agent", None, "Dhaka", agent.id)

    def tearDown(self):
        self.session.close()
        self.engine.dispose()

    def run_scenario(self, kind, provider, severity, is_anomaly):
        inject_scenario(
            {
                "kind": kind,
                "label": f"E2E {kind}",
                "provider": provider,
                "intended_severity": severity,
                "is_anomaly": is_anomaly,
                "duration_minutes": 8,
            },
            self.principal,
            self.session,
        )
        return run_cycle(8, self.principal, self.session)

    def test_liquidity_control_creates_provider_pressure(self):
        result = self.run_scenario("bkash_surge", "bkash", "critical", False)
        balance = self.session.exec(
            select(ProviderBalance)
            .where(ProviderBalance.agent_id == self.agent_id)
            .where(ProviderBalance.provider == "bkash")
        ).one()
        self.assertEqual(balance.balance, 0)
        self.assertTrue(any(row["provider"] == "bkash" for row in result["new_alerts"]))

    def test_repeated_amount_control_creates_explainable_review_signal(self):
        self.run_scenario("repeated_amount", "nagad", "high", True)
        events = self.session.exec(select(AnomalyEvent).where(AnomalyEvent.provider == "nagad")).all()
        repeated = next(event for event in events if event.rule == "repeated_amount")
        self.assertIn("Evidence transactions", " ".join(json.loads(repeated.reasons_json)))

    def test_threshold_control_uses_selected_provider(self):
        self.run_scenario("structuring", "bkash", "high", True)
        events = self.session.exec(select(AnomalyEvent).where(AnomalyEvent.provider == "bkash")).all()
        self.assertIn("structuring", {event.rule for event in events})

    def test_feed_delay_activates_safe_fallback(self):
        result = self.run_scenario("rocket_delay", "rocket", "low", False)
        self.assertLess(result["data_quality"]["rocket"], .5)
        forecast = self.session.exec(
            select(ForecastSnapshot)
            .where(ForecastSnapshot.provider == "rocket")
            .order_by(ForecastSnapshot.ts.desc())
        ).first()
        self.assertIsNone(forecast.hours_to_shortage)
        self.assertLess(forecast.confidence, .5)

    def test_salary_day_does_not_create_behavioral_anomaly(self):
        self.run_scenario("salary_day", "nagad", "normal", False)
        events = self.session.exec(select(AnomalyEvent).where(AnomalyEvent.provider == "nagad")).all()
        self.assertEqual(events, [])
        # A legitimate event may still create a liquidity alert; the negative
        # control promises only that volume alone is not labeled unusual.
        alerts = self.session.exec(select(Alert).where(Alert.provider == "nagad")).all()
        self.assertTrue(all(alert.initial_owner != "anomaly" for alert in alerts))


if __name__ == "__main__":
    unittest.main()
