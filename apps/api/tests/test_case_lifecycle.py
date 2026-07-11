import json
import unittest
from datetime import datetime, timedelta

from fastapi import HTTPException
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.models.database import Agent, Alert, Case
from app.routers.alerts import add_reviewer_note, coordinate_case, execute_recommended_action, get_alert, list_alerts, record_risk_recommendation, transition_case
from app.routers.notifications import list_notifications
from app.services.auth import Principal
from app.services.cases import open_case_for_alert, process_due_escalations


class CaseLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        SQLModel.metadata.create_all(self.engine)
        with Session(self.engine) as session:
            agent = Agent(code="TRACE", display_name="Trace agent", area="Dhaka")
            session.add(agent); session.commit(); session.refresh(agent)
            self.agent_id = agent.id
            alert = Alert(
                agent_id=agent.id, provider="bkash", severity="high", priority_score=75,
                title="bKash may face shortage", summary="Connected DSS alert",
                reasons_json=json.dumps(["Forecast: 20 min to shortage", "Human verification required"]),
                evidence_json=json.dumps([{"source": "forecast", "rule": "rate_projection", "text": "net burn 400 BDT/min"}]),
                confidence=.82,
                recommended_actions_json=json.dumps([{"key": "request_cash_support", "label": "Request Cash Support", "weight": .9}]),
                fused_explanation="High pressure; confidence 82%; human action required.",
                owner_role="ops", owner_label="Provider Operations", initial_owner="liquidity",
            )
            session.add(alert); session.commit(); session.refresh(alert)
            self.alert_id = alert.id
            open_case_for_alert(session, alert, owner_role=alert.owner_role, owner_label=alert.owner_label)

            unrelated = Alert(
                agent_id=agent.id, provider="rocket", severity="low", priority_score=40,
                title="Rocket liquidity", summary="Ops-only unrelated case", confidence=.8,
                owner_role="ops", owner_label="Provider Operations", initial_owner="liquidity",
            )
            session.add(unrelated); session.commit(); session.refresh(unrelated)
            self.unrelated_id = unrelated.id
            open_case_for_alert(session, unrelated, owner_role="ops", owner_label="Provider Operations")

            provider_alert = Alert(
                agent_id=agent.id, provider="bkash", severity="low", priority_score=40,
                title="bKash feed quality", summary="Provider response needed", confidence=.5,
                owner_role="provider", owner_label="Financial Service Provider — feed owner",
                initial_owner="data-quality",
            )
            session.add(provider_alert); session.commit(); session.refresh(provider_alert)
            self.provider_alert_id = provider_alert.id
            open_case_for_alert(session, provider_alert, owner_role="provider", owner_label=provider_alert.owner_label)

        self.ops = Principal("ops", "Operations", "ops", None, "Dhaka")
        self.risk = Principal("risk", "Risk", "risk", None, "Dhaka")

    def test_complete_persistent_human_trace_and_scope(self):
        with Session(self.engine) as session:
            # The receiving team sees a durable inbox reaction immediately,
            # and the Cases API includes the persistent case object.
            ops_inbox = list_notifications(self.ops, session)
            self.assertEqual(ops_inbox["unread"], 2)  # target case + unrelated ops case
            cases = list_alerts(principal=self.ops, session=session)["alerts"]
            self.assertTrue(any(row["id"] == self.alert_id and row.get("case", {}).get("state") == "assigned" for row in cases))

        with Session(self.engine) as session:
            agent = Principal("agent", "Agent", "agent", None, "Dhaka", self.agent_id)
            notified = execute_recommended_action(
                self.alert_id, {"action_key": "notify_ops", "note": "Please review the forecast-sized support need"},
                agent, session,
            )
            self.assertEqual(notified["case"]["state"], "assigned")
            ops_events = {n["event"] for n in list_notifications(self.ops, session)["notifications"] if n["alert_id"] == self.alert_id}
            self.assertIn("operations_notified", ops_events)

        # Each action uses a fresh session to prove persistence across re-fetches.
        steps = [
            (self.ops, "ack", "Operations acknowledged the alert"),
            (self.ops, "review", "Operations reviewed forecast evidence"),
            (self.ops, "escalate", "Human escalation to Risk for connected-signal review"),
        ]
        expected_states = ["acknowledged", "review", "escalated"]
        for (principal, action, note), expected in zip(steps, expected_states):
            with Session(self.engine) as session:
                result = transition_case(self.alert_id, {"action": action, "note": note}, principal, session)
                self.assertEqual(result["case"]["state"], expected)
            with Session(self.engine) as session:
                persisted = get_alert(self.alert_id, principal=self.risk if expected in expected_states[2:] else self.ops, session=session)
                self.assertEqual(persisted["case"]["state"], expected)

        with Session(self.engine) as session:
            recommendation = record_risk_recommendation(
                self.alert_id,
                {"recommendation": "requires_further_investigation", "comment": "Repeated values require additional contextual records"},
                self.risk,
                session,
            )
            self.assertEqual(recommendation["state"], "risk_review")

        with Session(self.engine) as session:
            final = get_alert(self.alert_id, principal=self.risk, session=session)
            self.assertEqual(final["status"], "risk_review")
            self.assertEqual(final["case"]["owner_role"], "risk")
            audit = final["case"]["audit"]
            state_changes = [row["to_state"] for row in audit if row["from_state"] != row["to_state"]]
            self.assertEqual(state_changes,
                             ["new", "assigned", "acknowledged", "review", "escalated", "risk_review"])
            self.assertEqual([row["ts"] for row in audit], sorted(row["ts"] for row in audit))
            self.assertTrue(all(row.get("actor") and row.get("actor_role") for row in audit))
            escalation = next(row for row in audit if row["to_state"] == "escalated")
            self.assertEqual((escalation["owner_from"], escalation["owner_to"]), ("ops", "risk"))

            risk_inbox = list_notifications(self.risk, session)
            self.assertTrue(any(n["event"] == "case_escalated" and n["alert_id"] == self.alert_id for n in risk_inbox["notifications"]))
            agent = Principal("agent", "Agent", "agent", None, "Dhaka", self.agent_id)
            agent_inbox = list_notifications(agent, session)
            agent_events = {n["event"] for n in agent_inbox["notifications"] if n["alert_id"] == self.alert_id}
            self.assertTrue({"case_routed", "case_acknowledged", "case_review", "risk_review_updated"}.issubset(agent_events))

            with self.assertRaises(HTTPException) as forbidden:
                transition_case(self.alert_id, {"action": "close", "note": "attempt"}, self.risk, session)
            self.assertEqual(forbidden.exception.status_code, 403)

            with self.assertRaises(HTTPException) as unrelated:
                get_alert(self.unrelated_id, principal=self.risk, session=session)
            self.assertEqual(unrelated.exception.status_code, 403)

    def test_target_provider_can_react_only_to_its_own_authorized_case(self):
        bkash = Principal("provider_bkash", "bKash", "provider", "bkash", "Dhaka")
        rocket = Principal("provider_rocket", "Rocket", "provider", "rocket", "Dhaka")
        with Session(self.engine) as session:
            self.assertEqual(transition_case(self.provider_alert_id, {"action": "ack", "note": "bKash received"}, bkash, session)["case"]["state"], "acknowledged")
            self.assertEqual(transition_case(self.provider_alert_id, {"action": "review", "note": "feed owner reviewing"}, bkash, session)["case"]["state"], "review")
            self.assertEqual(transition_case(self.provider_alert_id, {"action": "start", "note": "feed repair coordination started"}, bkash, session)["case"]["state"], "in_progress")
            self.assertEqual(transition_case(self.provider_alert_id, {"action": "resolve", "note": "feed restored by bKash"}, bkash, session)["case"]["state"], "resolved")
            self.assertEqual(transition_case(self.provider_alert_id, {"action": "close", "note": "feed restoration verified"}, bkash, session)["case"]["state"], "closed")
        with Session(self.engine) as session:
            with self.assertRaises(HTTPException) as denied:
                get_alert(self.provider_alert_id, principal=rocket, session=session)
            self.assertEqual(denied.exception.status_code, 403)

    def test_stakeholder_authority_boundaries_are_enforced(self):
        """A crafted API request must not bypass the stakeholder workflow."""
        agent = Principal("agent", "Agent", "agent", None, "Dhaka", self.agent_id)
        provider = Principal("provider_bkash", "bKash", "provider", "bkash", "Dhaka")
        management = Principal("management", "Management", "management", None, None)
        with Session(self.engine) as session:
            for principal, action in (
                (agent, "resolve"),       # agent can acknowledge, not coordinate closure
                (provider, "ack"),        # provider cannot own an operations case
                (self.risk, "ack"),       # risk cannot perform operations triage
                (management, "ack"),      # management is read-only
            ):
                with self.subTest(role=principal.role, action=action):
                    with self.assertRaises(HTTPException) as denied:
                        transition_case(
                            self.alert_id,
                            {"action": action, "note": "crafted request"},
                            principal,
                            session,
                        )
                    self.assertEqual(denied.exception.status_code, 403)

    def test_overdue_critical_case_is_routed_but_never_auto_decided(self):
        with Session(self.engine) as session:
            alert = session.get(Alert, self.alert_id)
            alert.severity = "critical"
            case = session.exec(select(Case).where(Case.alert_id == self.alert_id)).first()
            case.created_at = datetime.utcnow() - timedelta(minutes=11)
            session.add(alert); session.add(case); session.commit()
            self.assertEqual(process_due_escalations(session), 1)
            session.refresh(case)
            self.assertEqual(case.state, "escalated")
            self.assertEqual(case.owner_role, "risk")
            audit = json.loads(case.audit_json)
            self.assertEqual(audit[-1]["actor"], "escalation-engine")
            self.assertIn("10-minute", audit[-1]["reason"])

    def test_operations_coordination_assignment_notifications_and_audit(self):
        with Session(self.engine) as session:
            reassigned = coordinate_case(
                self.alert_id,
                {"action": "reassign", "target": "field_officer", "comment": "Visit outlet and verify service readiness"},
                self.ops,
                session,
            )
            self.assertEqual(reassigned["assigned_contact_type"], "field_officer")
            self.assertTrue(reassigned["contacts"]["field_officer"]["phone"].startswith("+880"))
            notified = coordinate_case(
                self.alert_id,
                {"action": "request_agent_confirmation", "comment": "Confirm current queue and available cash"},
                self.ops,
                session,
            )
            self.assertIn("coordination:request_agent_confirmation", notified["audit"][-1]["reason"])
            agent = Principal("agent", "Agent", "agent", None, "Dhaka", self.agent_id)
            events = {n["event"] for n in list_notifications(agent, session)["notifications"]}
            self.assertIn("coordination_request_agent_confirmation", events)
            with self.assertRaises(HTTPException) as denied:
                coordinate_case(self.alert_id, {"action": "notify_agent", "comment": "attempt"}, agent, session)
            self.assertEqual(denied.exception.status_code, 403)

    def test_note_persists_escalation_changes_owner_and_provider_wall_holds(self):
        with Session(self.engine) as session:
            add_reviewer_note(
                self.alert_id, {"text": "Outlet requested contextual review before escalation"},
                self.ops, session,
            )
        with Session(self.engine) as session:
            refreshed = get_alert(self.alert_id, principal=self.ops, session=session)
            self.assertTrue(any("contextual review" in n["text"] for n in refreshed["case"]["notes"]))
            transition_case(self.alert_id, {"action": "ack", "note": "Operations acknowledged"}, self.ops, session)
            transition_case(self.alert_id, {"action": "review", "note": "Evidence reviewed"}, self.ops, session)
            transition_case(self.alert_id, {"action": "escalate", "note": "Requires additional human review"}, self.ops, session)
        with Session(self.engine) as session:
            risk_rows = list_alerts(principal=self.risk, session=session)["alerts"]
            escalated = next(row for row in risk_rows if row["id"] == self.alert_id)
            self.assertEqual(escalated["status"], "escalated")
            self.assertEqual(escalated["case"]["owner_role"], "risk")

            bkash = Principal("provider_bkash", "bKash", "provider", "bkash", "Dhaka")
            rocket = Principal("provider_rocket", "Rocket", "provider", "rocket", "Dhaka")
            bkash_ids = {row["id"] for row in list_alerts(principal=bkash, session=session)["alerts"]}
            rocket_rows = list_alerts(principal=rocket, session=session)["alerts"]
            rocket_ids = {row["id"] for row in rocket_rows}
            self.assertIn(self.alert_id, bkash_ids)
            self.assertNotIn(self.unrelated_id, bkash_ids)
            self.assertIn(self.unrelated_id, rocket_ids)
            self.assertNotIn(self.alert_id, rocket_ids)
            untouched = next(row for row in rocket_rows if row["id"] == self.unrelated_id)
            self.assertEqual(untouched["case"]["state"], "assigned")
            self.assertEqual(untouched["case"]["owner_role"], "ops")


if __name__ == "__main__":
    unittest.main()
