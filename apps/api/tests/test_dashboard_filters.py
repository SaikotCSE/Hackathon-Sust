import unittest
from datetime import datetime

from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.models.database import Agent, ForecastSnapshot, ProviderBalance
from app.routers.dashboard import dashboard
from app.services.auth import Principal


class DashboardFilterTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        SQLModel.metadata.create_all(self.engine)
        self.session = Session(self.engine)
        agent = Agent(code="MGR-1", display_name="Filtered agent", area="Dhaka-Mirpur")
        self.session.add(agent)
        self.session.commit()
        self.session.refresh(agent)
        self.agent_id = agent.id
        self.session.add(ProviderBalance(agent_id=agent.id, provider="bkash", balance=3_000))
        self.session.add(ProviderBalance(agent_id=agent.id, provider="nagad", balance=99_000))
        self.session.add(ForecastSnapshot(
            agent_id=agent.id,
            provider="bkash",
            hours_to_shortage=0.5,
            confidence=.9,
            summary="elevated burn",
            reasons_json='["100 BDT/min"]',
            method="rate_projection",
            data_quality=1.0,
            burn_rate_per_min=100,
            ts=datetime.utcnow(),
        ))
        self.session.commit()
        self.management = Principal("mgmt", "Management", "management", None, None)

    def tearDown(self):
        self.session.close()
        self.engine.dispose()

    def test_provider_area_agent_and_time_filters_are_applied(self):
        result = dashboard(
            agent_id=1,
            area="Dhaka",
            provider="bkash",
            mgr_agent=self.agent_id,
            since_minutes=60,
            principal=self.management,
            session=self.session,
        )
        self.assertEqual(result["view"], "management")
        self.assertEqual(result["scope"]["filters"], {
            "area": "Dhaka",
            "provider": "bkash",
            "agent_id": self.agent_id,
            "since_minutes": 60,
        })
        self.assertEqual(result["scope"]["agent_count"], 1)
        row = result["areas"][0]["agents_detail"][0]
        self.assertEqual(row["provider_filter"], "bkash")
        self.assertEqual(row["overall_score"], 70)
        self.assertIn("pressure", row["overall_reason"])

    def test_provider_response_contains_only_its_own_position(self):
        provider = Principal("provider_bkash", "bKash", "provider", "bkash", "Dhaka")
        result = dashboard(
            agent_id=1,
            area=None,
            provider=None,
            mgr_agent=None,
            since_minutes=None,
            principal=provider,
            session=self.session,
        )
        row = result["per_agent"][0]
        self.assertEqual([p["provider"] for p in row["providers"]], ["bkash"])
        self.assertNotIn("physical_cash", row)
        self.assertNotIn("aggregate", row)
        self.assertEqual(row["overall_score"], 70)


if __name__ == "__main__":
    unittest.main()
