import unittest

from fastapi import HTTPException
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.models.database import Agent, ProviderBalance
from app.routers.scenarios import inject
from app.services.auth import Principal


class ScenarioControlTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        SQLModel.metadata.create_all(self.engine)
        self.session = Session(self.engine)
        agent = Agent(code="SCENARIO", display_name="Scenario agent", area="Dhaka")
        self.session.add(agent)
        self.session.commit()
        self.session.refresh(agent)
        self.agent_id = agent.id
        for provider in ("physical", "bkash", "nagad", "rocket"):
            self.session.add(ProviderBalance(
                agent_id=agent.id,
                provider=provider,
                balance=100_000,
            ))
        self.session.commit()
        self.principal = Principal("agent", "Agent", "agent", None, "Dhaka", agent.id)

    def tearDown(self):
        self.session.close()
        self.engine.dispose()

    def test_valid_control_returns_execution_metadata(self):
        result = inject(
            {
                "kind": "repeated_amount",
                "label": "Repeated Nagad amounts",
                "provider": "nagad",
                "intended_severity": "high",
                "is_anomaly": True,
                "duration_minutes": 8,
            },
            self.principal,
            self.session,
        )
        self.assertEqual(result["provider"], "nagad")
        self.assertEqual(result["duration_minutes"], 8)
        self.assertTrue(result["analysis_required"])

    def test_invalid_provider_and_duration_are_rejected(self):
        bad_payloads = [
            {"kind": "repeated_amount", "label": "bad provider", "provider": "other"},
            {"kind": "salary_day", "label": "bad duration", "provider": "nagad", "duration_minutes": 0},
            {"kind": "rocket_delay", "label": "wrong feed", "provider": "bkash"},
        ]
        for payload in bad_payloads:
            with self.subTest(payload=payload), self.assertRaises(HTTPException) as error:
                inject(payload, self.principal, self.session)
            self.assertEqual(error.exception.status_code, 400)


if __name__ == "__main__":
    unittest.main()
