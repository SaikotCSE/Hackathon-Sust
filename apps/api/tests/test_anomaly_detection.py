import json
import unittest
from datetime import datetime, timedelta

from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.models.database import Agent, OperationalContextEvent, ProviderBalance, ScenarioEvent, Transaction
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

    def test_ground_truth_label_is_not_a_detector_input(self):
        self.session.add(ScenarioEvent(
            agent_id=self.agent_id, provider="rocket", kind="salary_day",
            intended_severity="normal", is_anomaly_ground_truth=False,
            duration_minutes=10, note="evaluation label only",
        ))
        self.session.commit()
        self.add_transactions("rocket", [2375] * 6, "GT")
        events = detect_anomalies(self.session, self.agent_id, "rocket")
        self.assertIn("repeated_amount", {event.rule for event in events})

    def test_observed_context_suppresses_only_compatible_volume_rules(self):
        now = datetime.utcnow()
        for i, amount in enumerate([700, 1100, 1750]):
            self.session.add(Transaction(
                agent_id=self.agent_id, provider="nagad", tx_type="cash_out",
                amount=amount, counterparty_id=f"OLD{i}", area="Dhaka",
                ts=now - timedelta(minutes=15, seconds=i),
            ))
        for i, amount in enumerate([2400, 3200, 6800, 7600, 9200, 1200, 2100, 3600, 8800]):
            self.session.add(Transaction(
                agent_id=self.agent_id, provider="nagad", tx_type="cash_out",
                amount=amount, counterparty_id=f"NEW{i}", area="Dhaka",
                ts=now - timedelta(seconds=20 - i),
            ))
        self.session.commit()
        without_context = detect_anomalies(self.session, self.agent_id, "nagad")
        self.assertIn("velocity_spike", {event.rule for event in without_context})

        self.session.add(OperationalContextEvent(
            agent_id=self.agent_id, provider="nagad", kind="salary_day",
            note="Observed salary calendar", source="test-calendar",
            started_at=now - timedelta(minutes=1), ends_at=now + timedelta(minutes=9),
        ))
        self.session.commit()
        with_context = detect_anomalies(self.session, self.agent_id, "nagad")
        self.assertNotIn("velocity_spike", {event.rule for event in with_context})


if __name__ == "__main__":
    unittest.main()
