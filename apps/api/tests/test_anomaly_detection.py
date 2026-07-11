import json
import unittest
from datetime import datetime, timedelta

from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.models.database import Agent, ProviderBalance, ScenarioEvent, Transaction
from app.services.anomaly import detect_anomalies
from app.simulation.engine import ScenarioSpec, SimulationEngine


class AnomalyDetectionTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        SQLModel.metadata.create_all(self.engine)
        self.session = Session(self.engine)
        agent = Agent(code="ANOM", display_name="Anomaly test", area="Dhaka")
        self.session.add(agent); self.session.commit(); self.session.refresh(agent)
        self.agent_id = agent.id
        for provider, balance in (("bkash", 100_000), ("nagad", 100_000), ("rocket", 100_000), ("physical", 100_000)):
            self.session.add(ProviderBalance(agent_id=self.agent_id, provider=provider, balance=balance))
        self.session.commit()

    def tearDown(self):
        self.session.close(); self.engine.dispose()

    def add_transactions(self, provider, amounts, prefix):
        now = datetime.utcnow()
        for i, amount in enumerate(amounts):
            self.session.add(Transaction(
                agent_id=self.agent_id, provider=provider, tx_type="cash_out",
                amount=amount, counterparty_id=f"{prefix}{i % 3}", area="Dhaka",
                ts=now - timedelta(seconds=len(amounts) - i),
            ))
        self.session.commit()

    def test_obvious_rules_are_detected_with_record_level_evidence(self):
        self.add_transactions("bkash", [2375] * 6, "R")
        repeated = detect_anomalies(self.session, self.agent_id, "bkash")
        self.assertIn("repeated_amount", {event.rule for event in repeated})
        reasons = json.loads(next(event for event in repeated if event.rule == "repeated_amount").reasons_json)
        self.assertTrue(any("tx#" in reason and "R" in reason for reason in reasons))
        self.assertTrue(any("Uncertainty:" in reason and "requires human review" in reason for reason in reasons))

        self.add_transactions("nagad", [4910, 4950, 4990, 5030, 5070], "S")
        structured = detect_anomalies(self.session, self.agent_id, "nagad")
        self.assertIn("structuring", {event.rule for event in structured})

    def test_injected_scenario_survives_new_engine_instance(self):
        first = SimulationEngine(self.session, self.agent_id)
        first.inject_scenario(ScenarioSpec(
            kind="structuring", label="labeled split pattern", provider="bkash",
            intended_severity="high", is_anomaly_ground_truth=True, duration_minutes=5,
        ))
        # Mirrors the API: /inject and /tick construct different engines.
        second = SimulationEngine(self.session, self.agent_id)
        second.tick(n_transactions=6)
        events = detect_anomalies(self.session, self.agent_id, "bkash")
        self.assertIn("structuring", {event.rule for event in events})

    def test_declared_legitimate_high_volume_is_not_flagged(self):
        for provider, kind in (("rocket", "salary_day"), ("nagad", "bkash_surge")):
            self.session.add(ScenarioEvent(
                agent_id=self.agent_id, provider=provider, kind=kind,
                intended_severity="normal", is_anomaly_ground_truth=False,
                duration_minutes=10, note="known legitimate demand",
            ))
            self.session.commit()
            self.add_transactions(provider, [700, 1100, 1750, 2400, 3200, 6800, 7600, 9200] * 2, "N")
            self.assertEqual(detect_anomalies(self.session, self.agent_id, provider), [])


if __name__ == "__main__":
    unittest.main()
