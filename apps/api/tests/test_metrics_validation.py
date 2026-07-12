import unittest
from datetime import datetime, timedelta

from app.models.database import Alert, ScenarioEvent
from app.services.metrics import _pr_metrics, _priority_alignment


class MetricsValidationTests(unittest.TestCase):
    def test_empty_evaluation_is_not_reported_as_perfect(self):
        self.assertEqual(_pr_metrics([], []), (None, None, None))

    def test_priority_alignment_uses_same_agent_provider_and_window(self):
        now = datetime.utcnow()
        scenario = ScenarioEvent(
            agent_id=1,
            provider="bkash",
            kind="repeated_amount",
            intended_severity="high",
            is_anomaly_ground_truth=True,
            duration_minutes=5,
            injected_at=now,
        )
        unrelated_high = Alert(
            agent_id=2,
            provider="bkash",
            severity="high",
            priority_score=90,
            title="Unrelated",
            summary="Unrelated",
            confidence=.8,
            owner_role="ops",
            owner_label="Operations",
            initial_owner="anomaly",
            updated_at=now + timedelta(minutes=1),
        )
        related_low = Alert(
            agent_id=1,
            provider="bkash",
            severity="low",
            priority_score=40,
            title="Related",
            summary="Related",
            confidence=.7,
            owner_role="ops",
            owner_label="Operations",
            initial_owner="anomaly",
            updated_at=now + timedelta(minutes=1),
        )
        self.assertEqual(_priority_alignment([scenario], [unrelated_high, related_low]), 0.0)
        related_low.severity = "high"
        self.assertEqual(_priority_alignment([scenario], [unrelated_high, related_low]), 1.0)


if __name__ == "__main__":
    unittest.main()
