import unittest

from app.services.decision_weights import _owner_for


class OwnershipMappingTests(unittest.TestCase):
    def test_liquidity_has_an_owner_at_every_severity(self):
        for severity in ("normal", "low", "high", "critical"):
            role, label = _owner_for("liquidity", severity)
            self.assertEqual(role, "ops")
            self.assertTrue(label)

    def test_unusual_activity_starts_with_operations_triage(self):
        for severity in ("low", "high", "critical"):
            role, label = _owner_for("anomaly", severity)
            self.assertEqual(role, "ops")
            self.assertIn("initial triage", label.lower())


if __name__ == "__main__":
    unittest.main()
