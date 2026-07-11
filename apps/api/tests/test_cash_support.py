import unittest

from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.models.database import Agent, Alert, Case, CashSupportRequest, ForecastSnapshot, ProviderBalance
from app.routers.alerts import execute_recommended_action
from app.routers.cash_support import list_requests, provider_action
from app.routers.notifications import list_notifications
from app.services.auth import Principal


class CashSupportWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        SQLModel.metadata.create_all(self.engine)
        self.session = Session(self.engine)
        agent = Agent(code="AGT-1", display_name="Agent One", area="Dhaka")
        self.session.add(agent); self.session.commit(); self.session.refresh(agent)
        alert = Alert(
            agent_id=agent.id, provider="bkash", severity="high", priority_score=75,
            title="bKash pressure", summary="shortage likely", confidence=.9,
            owner_role="ops", owner_label="Ops", initial_owner="liquidity",
        )
        self.session.add(alert); self.session.commit(); self.session.refresh(alert)
        case = Case(alert_id=alert.id, state="assigned", owner_role="ops", owner_label="Ops")
        self.session.add(case)
        self.session.add(ProviderBalance(agent_id=agent.id, provider="bkash", balance=1000))
        self.session.add(ProviderBalance(agent_id=agent.id, provider="physical", balance=50_000))
        self.session.add(ForecastSnapshot(
            agent_id=agent.id, provider="bkash", hours_to_shortage=1,
            confidence=.9, method="rate_projection", data_quality=1,
            burn_rate_per_min=20,
        ))
        self.session.commit()
        self.agent_id, self.alert_id = agent.id, alert.id

    def tearDown(self):
        self.session.close(); self.engine.dispose()

    def test_agent_request_notifies_only_target_provider_and_provider_completes(self):
        agent = Principal("agent", "Agent", "agent", None, None, self.agent_id)
        result = execute_recommended_action(
            self.alert_id, {"action_key": "request_cash_support", "note": "Need float urgently"},
            principal=agent, session=self.session,
        )
        self.assertEqual(result["cash_support_status"], "requested")

        bkash = Principal("provider_bkash", "bKash", "provider", "bkash", None)
        rocket = Principal("provider_rocket", "Rocket", "provider", "rocket", None)
        visible = list_requests(principal=bkash, session=self.session)["requests"]
        hidden = list_requests(principal=rocket, session=self.session)["requests"]
        self.assertEqual(len(visible), 1)
        self.assertEqual(hidden, [])
        self.assertEqual(visible[0]["amount"], 9000)
        self.assertIn("8h demand", visible[0]["calculation"])

        request_id = visible[0]["id"]
        approved = provider_action(request_id, {"action": "approve", "note": "Support approved"}, bkash, self.session)
        self.assertEqual(approved["status"], "approved")
        self.assertEqual(approved["applied_amount"], 0)
        self.assertIsNone(approved["balance_after"])
        balance = self.session.exec(select(ProviderBalance).where(ProviderBalance.agent_id == self.agent_id).where(ProviderBalance.provider == "bkash")).first()
        self.assertEqual(balance.balance, 1_000)
        fulfilled = provider_action(request_id, {"action": "fulfil"}, bkash, self.session)
        self.assertEqual(fulfilled["status"], "fulfilled")
        self.assertEqual(self.session.get(ProviderBalance, balance.id).balance, 1_000)
        inbox = list_notifications(agent, self.session)
        events = {row["event"] for row in inbox["notifications"]}
        self.assertTrue({"cash_support_approved", "cash_support_fulfilled"}.issubset(events))
        case = self.session.exec(select(Case).where(Case.alert_id == self.alert_id)).first()
        self.assertIn("cash_support_fulfilled", case.audit_json)


if __name__ == "__main__":
    unittest.main()
