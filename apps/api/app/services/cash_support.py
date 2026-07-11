"""Forecast-sized provider-support coordination pipeline.

This module deliberately sizes and records a request only. It never changes a
wallet balance; fulfilment means the human coordination task was recorded complete.
"""
import math

from sqlmodel import Session, select

from ..models.database import CashSupportRequest, ForecastSnapshot, ProviderBalance
from ..simulation.engine import BASELINE

SUPPORT_COVERAGE_HOURS = 8.0


def size_request(session: Session, row: CashSupportRequest, *, override_amount=None) -> CashSupportRequest:
    balance = session.exec(
        select(ProviderBalance)
        .where(ProviderBalance.agent_id == row.agent_id)
        .where(ProviderBalance.provider == row.provider)
    ).first()
    if balance is None:
        raise ValueError("provider balance is unavailable")
    forecast = session.exec(
        select(ForecastSnapshot)
        .where(ForecastSnapshot.agent_id == row.agent_id)
        .where(ForecastSnapshot.provider == row.provider)
        .order_by(ForecastSnapshot.ts.desc()).limit(1)
    ).first()
    burn = max(0.0, float(forecast.burn_rate_per_min if forecast else 0.0))
    baseline = float(BASELINE.get(row.provider, 0.0))
    demand_target = burn * 60.0 * SUPPORT_COVERAGE_HOURS
    target = max(baseline, demand_target)
    required = max(0.0, target - float(balance.balance))
    forecast_amount = math.ceil(required / 500.0) * 500.0 if required > 0 else 0.0
    manual = float(override_amount) if override_amount is not None else None
    amount = manual if manual is not None and manual > 0 else forecast_amount

    row.amount = amount
    row.forecast_balance = float(balance.balance)
    row.forecast_burn_rate_per_min = burn
    row.coverage_hours = SUPPORT_COVERAGE_HOURS
    row.target_balance = target
    source = "operator override" if manual is not None and manual > 0 else "forecast-derived"
    row.calculation = (
        f"{source}: max(provider baseline {baseline:,.0f}, "
        f"{SUPPORT_COVERAGE_HOURS:.0f}h demand {burn:.2f} BDT/min × 480 min = {demand_target:,.0f}) "
        f"− current balance {balance.balance:,.0f}; rounded up to nearest 500 BDT"
    )
    session.add(row)
    return row
