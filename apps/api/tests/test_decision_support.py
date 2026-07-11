import json
import unittest

from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.models.database import Agent, Alert, AnomalyEvent, Case, ForecastSnapshot
from app.services.cases import CaseTransition, transition
from app.services.decision_weights import FusionInput, fuse
from app.services.orchestrator import build_alert_for_provider


class DecisionSupportTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        SQLModel.metadata.create_all(self.engine)
        self.session = Session(self.engine)
        agent = Agent(code="DSS", display_name="Decision test", area="Dhaka")
        self.session.add(agent); self.session.commit(); self.session.refresh(agent)
        self.agent_id = agent.id

    def tearDown(self):
        self.session.close(); self.engine.dispose()

    def forecast(self, hours, confidence=.9, provider="bkash"):
        row = ForecastSnapshot(
            agent_id=self.agent_id, provider=provider, hours_to_shortage=hours,
            confidence=confidence, summary="bKash draining under current demand",
            reasons_json=json.dumps(["net burn 400 BDT/min from recent balances"]),
            method="rate_projection", data_quality=1.0, burn_rate_per_min=400,
        )
        self.session.add(row); self.session.commit(); self.session.refresh(row)
        return row

    def anomaly(self, confidence=.85, provider="bkash"):
        row = AnomalyEvent(
            agent_id=self.agent_id, provider=provider, rule="repeated_amount",
            confidence=confidence, count=6, amount=2375, window_minutes=15,
            reasons_json=json.dumps([
                "Repeated 6 transactions",
                "Evidence transactions: tx#11 C01 2375 BDT; tx#12 C02 2375 BDT",
                "Uncertainty: fixed-price services can repeat; requires human review",
            ]),
        )
        self.session.add(row); self.session.commit(); self.session.refresh(row)
        return row

    def test_combined_signals_create_one_cross_referenced_alert(self):
        forecast = self.forecast(.25)
        first = build_alert_for_provider(
            self.session, self.agent_id, "bkash", data_quality=1.0,
            forecast=forecast, anomaly_events=[],
        )
        combined = build_alert_for_provider(
            self.session, self.agent_id, "bkash", data_quality=1.0,
            forecast=forecast, anomaly_events=[self.anomaly()],
        )
        self.assertEqual(first.id, combined.id)
        self.assertEqual(len(self.session.exec(select(Alert)).all()), 1)
        self.assertIn("shortage", combined.title.lower())
        self.assertIn("unusual activity", combined.title.lower())
        sources = {item["source"] for item in json.loads(combined.evidence_json)}
        self.assertEqual(sources, {"forecast", "anomaly"})
        keys = {item["key"] for item in json.loads(combined.recommended_actions_json)}
        self.assertIn("request_cash_support", keys)
        self.assertIn("risk_review", keys)
        self.assertEqual(combined.owner_role, "ops")
        self.assertGreater(combined.confidence, 0)
        case = self.session.exec(select(Case).where(Case.alert_id == combined.id)).first()
        transition(self.session, CaseTransition(case.id, "ack", "ops", "operator", "received"))
        transition(self.session, CaseTransition(case.id, "review", "ops", "operator", "reviewed both signals"))
        transition(self.session, CaseTransition(case.id, "escalate", "ops", "operator", "requires independent review"))
        with self.assertRaises(ValueError):
            transition(self.session, CaseTransition(case.id, "decision", "risk", "reviewer", "not permitted"))
        self.assertEqual(self.session.get(Alert, combined.id).status, "escalated")

    def test_recommendations_follow_signal_type_and_low_confidence_monitors(self):
        liquidity = fuse(FusionInput(
            0, .9, ["fast burn"], 0, [], 1, 1, "bkash", "liquidity"
        ))
        self.assertIn("request_cash_support", {a["key"] for a in liquidity.ranked_actions})
        self.assertNotIn("risk_review", {a["key"] for a in liquidity.ranked_actions})

        unusual = fuse(FusionInput(
            None, .85, ["stable"], .9, ["repeated values"], 0, 1, "rocket", "anomaly"
        ))
        unusual_keys = {a["key"] for a in unusual.ranked_actions}
        self.assertIn("risk_review", unusual_keys)
        self.assertNotIn("request_cash_support", unusual_keys)
        self.assertEqual(unusual.owner_role, "ops")

        shaky = fuse(FusionInput(
            .1, .2, ["sparse history"], 0, [], .8, 1, "nagad", "liquidity"
        ))
        self.assertEqual([a["key"] for a in shaky.ranked_actions], ["monitor"])
        self.assertLessEqual(shaky.confidence, .2)
        self.assertIn("uncertainty", " ".join(shaky.reasons).lower())

    def test_explanation_coverage_for_sample_alert_batch(self):
        alerts = [
            build_alert_for_provider(self.session, self.agent_id, "bkash", data_quality=1,
                                     forecast=self.forecast(0, provider="bkash"), anomaly_events=[]),
            build_alert_for_provider(self.session, self.agent_id, "nagad", data_quality=1,
                                     forecast=self.forecast(None, provider="nagad"), anomaly_events=[self.anomaly(.9, "nagad")]),
            build_alert_for_provider(self.session, self.agent_id, "rocket", data_quality=1,
                                     forecast=self.forecast(.2, provider="rocket"), anomaly_events=[self.anomaly(.8, "rocket")]),
        ]
        covered = sum(bool(
            json.loads(a.reasons_json) and json.loads(a.evidence_json)
            and json.loads(a.recommended_actions_json) and a.owner_role
            and a.confidence > 0 and "Confidence:" in a.fused_explanation
        ) for a in alerts)
        self.assertEqual(covered / len(alerts), 1.0)


if __name__ == "__main__":
    unittest.main()
