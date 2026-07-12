"""Layer 6 — Explainability coverage sweep.

Maps to ``testing-scripts-prompt.md`` §6.

Sweeps every alert produced by a full sample run and computes the percentage
with a populated reason + evidence + uncertainty/confidence field. Target is
100%. CI fails if any alert is missing these fields (§7 mandatory + §12
metric "Alert explanation coverage").
"""
from __future__ import annotations

import json
from typing import Any, Dict, List

import pytest

from tests.helpers.api_client import APIClient
from tests.helpers.metrics import SINK

pytestmark = pytest.mark.layer6_explainability


def _flatten_alerts(payload: Any) -> List[Dict[str, Any]]:
    """Pull every alert-shaped object out of an API response."""
    out: List[Dict[str, Any]] = []
    if isinstance(payload, list):
        out.extend(a for a in payload if isinstance(a, dict))
    elif isinstance(payload, dict):
        for key in ("results", "items", "alerts", "data"):
            v = payload.get(key)
            if isinstance(v, list):
                out.extend(a for a in v if isinstance(a, dict))
        if not out and any(k in payload for k in ("reason", "evidence", "id")):
            out.append(payload)
    return out


def _has_explanation(alert: Dict[str, Any]) -> bool:
    """Reason + evidence + uncertainty must all be populated."""
    def _present(*keys: str) -> bool:
        for k in keys:
            v = alert.get(k)
            if v not in (None, "", [], {}):
                return True
        return False
    return (
        _present("reason", "summary", "message")
        and _present("evidence", "evidence_items", "supporting_evidence", "details")
        and _present("confidence", "uncertainty", "confidence_score")
    )


class TestAlertExplanationCoverage:
    def test_alerts_have_reason_evidence_uncertainty(self, api_client: APIClient,
                                                       require_backend, require_token):
        resp = api_client.get("alerts", expect=(200, 401, 403),
                               label="explanation_sweep")
        if resp.status_code != 200:
            pytest.skip("alerts list not reachable")
        alerts = _flatten_alerts(resp.json())
        if not alerts:
            pytest.skip("no alerts produced yet — run the seeded scenario first")
        gaps = [a.get("id", "<no-id>") for a in alerts if not _has_explanation(a)]
        coverage = (len(alerts) - len(gaps)) / len(alerts) * 100.0
        SINK.set(
            "alert_explanation_coverage",
            {
                "coverage_pct": round(coverage, 2),
                "total_alerts": len(alerts),
                "missing_explanation": gaps,
            },
            method="Layer 6 explainability sweep on /alerts",
        )
        assert not gaps, f"alerts missing explanation fields: {gaps}"

    def test_alert_detail_endpoint_has_explanation(self, api_client: APIClient,
                                                     require_backend, require_token):
        # Fetch one alert detail (if any IDs known) and assert it carries all three.
        resp = api_client.get("alerts", expect=(200, 401, 403),
                               label="detail_pickup")
        if resp.status_code != 200:
            pytest.skip("alerts list not reachable")
        alerts = _flatten_alerts(resp.json())
        if not alerts:
            pytest.skip("no alerts to inspect")
        first_id = alerts[0].get("id")
        if not first_id:
            pytest.skip("alerts have no id field")
        detail = api_client.get(f"alerts/{first_id}", expect=(200, 401, 403, 404),
                                 label="alert_detail").json()
        assert _has_explanation(detail), detail