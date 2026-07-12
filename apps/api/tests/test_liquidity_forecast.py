import time
import unittest
from datetime import datetime, timedelta

from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.models.database import Agent, BalanceHistory, ProviderBalance
from app.services.liquidity import compute_forecast, rate_projection
from app.services.snapshots import agent_snapshot, operational_liquidity_summary
from app.simulation.engine import ScenarioSpec, SimulationEngine, data_quality_for, resolve_open_data_quality


class LiquidityForecastTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite://", connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        SQLModel.metadata.create_all(self.engine)
        self.session = Session(self.engine)
        agent = Agent(code="TEST", display_name="Test", area="Dhaka")
        self.session.add(agent)
        self.session.commit()
        self.session.refresh(agent)
        self.agent_id = agent.id
        self.t0 = datetime(2026, 1, 1, 12, 0)

    def tearDown(self):
        self.session.close()

    def add_series(self, provider, balances, minute_offsets=None):
        offsets = minute_offsets if minute_offsets is not None else range(len(balances))
        for offset, balance in zip(offsets, balances):
            self.session.add(BalanceHistory(
                agent_id=self.agent_id, provider=provider, balance=float(balance),
                physical_cash=50_000.0, ts=self.t0 + timedelta(minutes=offset),
            ))
        self.session.commit()

    def test_eta_is_derived_from_net_trend(self):
        # Raw series falls exactly ৳100/min: at minute 10, ৳9,000 / 100 = 90 min.
        self.add_series("bkash", [10_000 - 100 * i for i in range(11)])
        result = rate_projection(
            self.session, self.agent_id, "bkash", now=self.t0 + timedelta(minutes=10)
        )
        self.assertAlmostEqual(result.burn_rate_per_min, 100.0, places=6)
        self.assertAlmostEqual(result.hours_to_shortage, 1.5, places=6)
        self.assertGreaterEqual(result.confidence, 0.9)

    def test_provider_and_physical_are_independent(self):
        self.add_series("bkash", [6_000 - 200 * i for i in range(11)])
        self.add_series("nagad", [80_000 + 10 * i for i in range(11)])
        self.add_series("physical", [20_000 - 50 * i for i in range(11)])
        now = self.t0 + timedelta(minutes=10)
        bkash = rate_projection(self.session, self.agent_id, "bkash", now=now)
        nagad = rate_projection(self.session, self.agent_id, "nagad", now=now)
        physical = rate_projection(self.session, self.agent_id, "physical", now=now)
        self.assertAlmostEqual(bkash.hours_to_shortage * 60, 20.0, places=6)
        self.assertIsNone(nagad.hours_to_shortage)
        self.assertAlmostEqual(physical.hours_to_shortage * 60, 390.0, places=6)

    def test_same_engine_distinguishes_stable_and_fast_drain_inputs(self):
        self.add_series("stable", [20_000] * 11)
        self.add_series("fast", [6_000 - 500 * i for i in range(11)])
        now = self.t0 + timedelta(minutes=10)

        stable = rate_projection(self.session, self.agent_id, "stable", now=now)
        fast = rate_projection(self.session, self.agent_id, "fast", now=now)

        self.assertEqual(stable.burn_rate_per_min, 0.0)
        self.assertIsNone(stable.hours_to_shortage)
        self.assertIn("stable", stable.summary)
        self.assertIn("no outflow", " ".join(stable.reasons))

        self.assertAlmostEqual(fast.burn_rate_per_min, 500.0, places=6)
        self.assertAlmostEqual(fast.hours_to_shortage * 60, 2.0, places=6)
        self.assertIn("draining fast", fast.summary)
        self.assertIn("500 BDT/min", " ".join(fast.reasons))
        self.assertNotEqual(stable.summary, fast.summary)

    def test_aggregate_pressure_uses_earliest_independent_constraint(self):
        summary = operational_liquidity_summary([
            {"provider": "physical", "balance": 50_000, "hours_to_shortage": 4.0,
             "forecast_confidence": .9, "data_quality": 1.0, "degraded": False},
            {"provider": "bkash", "balance": 2_000, "hours_to_shortage": .75,
             "forecast_confidence": .8, "data_quality": 1.0, "degraded": False},
            {"provider": "nagad", "balance": 80_000, "hours_to_shortage": None,
             "forecast_confidence": .85, "data_quality": 1.0, "degraded": False},
            {"provider": "rocket", "balance": 40_000, "hours_to_shortage": 3.0,
             "forecast_confidence": .7, "data_quality": 1.0, "degraded": False},
        ])
        self.assertEqual(summary["limiting_position"], "bkash")
        self.assertEqual(summary["limiting_hours_to_shortage"], .75)
        self.assertTrue(summary["non_convertible"])
        self.assertNotIn("total_cash", summary)

    def test_sparse_stale_and_conflicting_data_withhold_eta(self):
        self.add_series("bkash", [1000, 900, 800])
        sparse = rate_projection(self.session, self.agent_id, "bkash", now=self.t0 + timedelta(minutes=2))
        self.assertIsNone(sparse.hours_to_shortage)
        self.assertEqual(sparse.confidence, 0.0)

        self.add_series("rocket", [5000 - 100 * i for i in range(6)])
        stale = rate_projection(self.session, self.agent_id, "rocket", now=self.t0 + timedelta(minutes=30))
        self.assertIsNone(stale.hours_to_shortage)
        self.assertIn("stale", stale.summary)

        self.add_series("nagad", [5000, 4900, 4800, 4700, 4600])
        self.session.add(BalanceHistory(
            agent_id=self.agent_id, provider="nagad", balance=9000,
            physical_cash=50_000, ts=self.t0 + timedelta(minutes=4),
        ))
        self.session.commit()
        conflict = rate_projection(self.session, self.agent_id, "nagad", now=self.t0 + timedelta(minutes=4))
        self.assertIsNone(conflict.hours_to_shortage)
        self.assertEqual(conflict.confidence, 0.0)
        self.assertIn("conflicting", conflict.summary)

    def test_sudden_shortage_lead_time_and_balance_error(self):
        # Other wallets remain healthy while bKash drains at ৳100/min.
        self.add_series("bkash", [12_000 - 100 * i for i in range(5)])
        self.add_series("nagad", [80_000] * 5)
        self.add_series("rocket", [40_000] * 5)
        detected_at = self.t0 + timedelta(minutes=4)
        forecast = rate_projection(self.session, self.agent_id, "bkash", now=detected_at)
        actual_depletion = self.t0 + timedelta(minutes=120)
        lead_minutes = (actual_depletion - detected_at).total_seconds() / 60
        self.assertAlmostEqual(forecast.hours_to_shortage * 60, lead_minutes, places=6)
        self.assertIsNone(rate_projection(self.session, self.agent_id, "nagad", now=detected_at).hours_to_shortage)

        errors = []
        balance_at_detection = 11_600
        for checkpoint in (30, 60, 90):
            elapsed = checkpoint - 4
            projected = max(0, balance_at_detection - forecast.burn_rate_per_min * elapsed)
            actual = max(0, 12_000 - 100 * checkpoint)
            errors.append(abs(projected - actual))
        self.assertEqual(sum(errors) / len(errors), 0.0)

    def test_full_demonstrated_volume_is_responsive(self):
        # 10 agents × 4 reserves × 60 points mirrors the seeded demo volume.
        for agent_index in range(10):
            agent_id = self.agent_id
            if agent_index:
                agent = Agent(code=f"TEST-{agent_index}", display_name="Test", area="Dhaka")
                self.session.add(agent)
                self.session.commit()
                self.session.refresh(agent)
                agent_id = agent.id
            for provider in ("bkash", "nagad", "rocket", "physical"):
                for minute in range(60):
                    self.session.add(BalanceHistory(
                        agent_id=agent_id, provider=provider,
                        balance=100_000 - minute * 10, physical_cash=100_000,
                        ts=self.t0 + timedelta(minutes=minute),
                    ))
        self.session.commit()
        started = time.perf_counter()
        for agent_id in range(1, 11):
            for provider in ("bkash", "nagad", "rocket", "physical"):
                rate_projection(self.session, agent_id, provider, now=self.t0 + timedelta(minutes=59))
        elapsed_ms = (time.perf_counter() - started) * 1000
        self.assertLess(elapsed_ms, 1000, f"40 projections took {elapsed_ms:.1f}ms")

    def test_injected_delay_visibly_enters_fallback_and_recovers(self):
        now = datetime.utcnow()
        for provider, opening in (("bkash", 12_000), ("nagad", 20_000), ("rocket", 15_000), ("physical", 30_000)):
            self.session.add(ProviderBalance(agent_id=self.agent_id, provider=provider, balance=opening - 700))
            for i in range(8):
                self.session.add(BalanceHistory(
                    agent_id=self.agent_id, provider=provider,
                    balance=opening - i * 100, physical_cash=29_300,
                    ts=now - timedelta(minutes=7 - i),
                ))
            compute_forecast(self.session, self.agent_id, provider, data_quality=1.0)
        self.session.commit()
        clean_before = next(p for p in agent_snapshot(self.session, self.agent_id)["providers"] if p["provider"] == "rocket")
        self.assertFalse(clean_before["degraded"])

        SimulationEngine(self.session, self.agent_id).inject_scenario(ScenarioSpec(
            kind="rocket_delay", label="late feed", provider="rocket", duration_minutes=8,
        ))
        degraded_quality = data_quality_for(self.session, "rocket")
        self.assertLess(degraded_quality, 0.5)
        degraded_forecast = compute_forecast(self.session, self.agent_id, "rocket", data_quality=degraded_quality)
        degraded_snapshot = agent_snapshot(self.session, self.agent_id)
        rocket_degraded = next(p for p in degraded_snapshot["providers"] if p["provider"] == "rocket")
        self.assertIsNone(degraded_forecast.hours_to_shortage)
        self.assertLess(degraded_forecast.confidence, clean_before["forecast_confidence"])
        self.assertTrue(rocket_degraded["degraded"])
        self.assertTrue(degraded_snapshot["aggregate"]["fallback_active"])
        self.assertTrue(degraded_snapshot["aggregate"]["non_convertible"])
        self.assertNotIn("total_cash", degraded_snapshot["aggregate"])

        self.assertEqual(resolve_open_data_quality(self.session, "rocket"), 1)
        restored_quality = data_quality_for(self.session, "rocket")
        compute_forecast(self.session, self.agent_id, "rocket", data_quality=restored_quality)
        clean_after = next(p for p in agent_snapshot(self.session, self.agent_id)["providers"] if p["provider"] == "rocket")
        self.assertEqual(restored_quality, 1.0)
        self.assertFalse(clean_after["degraded"])
        self.assertGreater(clean_after["forecast_confidence"], rocket_degraded["forecast_confidence"])


if __name__ == "__main__":
    unittest.main()
