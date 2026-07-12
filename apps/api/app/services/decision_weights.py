"""Decision Intelligence Engine — Module 4.

Loads fusion weights from config/decision-weights.json and exposes pure helpers
that turn (forecast, anomaly, context, data_quality) into a single prioritized,
explainable recommendation. All weights are policy-driven, not learned.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

_CONFIG_PATH = Path(__file__).resolve().parents[4] / "config" / "decision-weights.json"


def _load_raw() -> dict:
    if not _CONFIG_PATH.exists():
        # Fallback to a baked-in default so the API never crashes on a missing file.
        return {
            "default": {
                "liquidityForecastWeight": 0.35,
                "anomalyWeight": 0.25,
                "customerImpactWeight": 0.20,
                "confidenceWeight": 0.10,
                "dataQualityWeight": 0.10,
            }
        }
    with _CONFIG_PATH.open("r", encoding="utf-8") as fh:
        return json.load(fh)


_RAW = _load_raw()


def reload_weights() -> dict:
    """Re-read the config from disk. The whole point of putting weights in config
    is that providers / ops can adjust them without code changes — the API exposes
    this as POST /config/reload."""
    global _RAW
    _RAW = _load_raw()
    return _RAW


def weights_for(provider: str) -> Dict[str, float]:
    provider = (provider or "").lower()
    base = dict(_RAW.get("default", {}))
    overrides = _RAW.get("overrides", {}).get(provider, {})
    base.update(overrides)
    # Strip comments & non-weight keys the loader might have surfaced.
    keep = {
        "liquidityForecastWeight",
        "anomalyWeight",
        "customerImpactWeight",
        "confidenceWeight",
        "dataQualityWeight",
    }
    return {k: float(v) for k, v in base.items() if k in keep}


def actions_catalog() -> List[Dict]:
    return list(_RAW.get("_actions", []))


def tier_for(score: int) -> str:
    """0..100 score → tier name. Same bands Module 5 renders."""
    if score >= 81:
        return "critical"
    if score >= 61:
        return "high"
    if score >= 31:
        return "low"
    return "normal"


# ---------------------------------------------------------------------------
# Input container — what the rest of the system feeds in
# ---------------------------------------------------------------------------

@dataclass
class FusionInput:
    forecast_hours_to_shortage: Optional[float]   # None = no projection
    forecast_confidence: float                   # 0..1
    forecast_reasons: List[str]

    anomaly_confidence: float                     # 0..1 (rule heads + IF blended)
    anomaly_reasons: List[str]

    customer_impact: float                       # 0..1 (proxy: estimated customers affected)
    data_quality: float                          # 0..1, 1 = healthy

    provider: str
    initial_owner: str                           # "liquidity" | "anomaly" | "data-quality"
    customers_waiting: int = 0
    area: str = ""
    festival: bool = False
    peak_hour: bool = False


@dataclass
class FusionOutput:
    priority_score: int                          # 0..100
    severity: str                                # normal | low | high | critical
    confidence: float
    fused_explanation: str
    ranked_actions: List[Dict]                   # [{key, label, weight}, ...]
    owner_role: str
    owner_label: str
    reasons: List[str]


# ---------------------------------------------------------------------------
# Engines
# ---------------------------------------------------------------------------

def _normalize_weights(w: Dict[str, float]) -> Dict[str, float]:
    total = sum(w.values()) or 1.0
    return {k: v / total for k, v in w.items()}


def _urgency_from_forecast(hours_to_shortage: Optional[float]) -> float:
    """Map 'hours to shortage' to a 0..1 urgency. None = 0 (no signal)."""
    if hours_to_shortage is None:
        return 0.0
    if hours_to_shortage <= 0:
        return 1.0
    # 0.5h → ~0.92, 1h → 0.78, 4h → 0.45, 12h → 0.20
    return max(0.0, min(1.0, 1.0 / (1.0 + hours_to_shortage / 1.0)))


def _severity_modifier(initial_owner: str, festival: bool, peak_hour: bool) -> float:
    """Small bump for context that pushes a borderline alert into a higher tier."""
    mod = 0.0
    if festival:
        mod += 0.05
    if peak_hour:
        mod += 0.03
    # Unusual-activity signal + festival is where context-aware distinction
    # matters most (legitimate spike vs review-worthy pattern). Don't double-penalize.
    return mod


def _pick_actions(
    *,
    severity: str,
    initial_owner: str,
    data_quality: float,
    has_liquidity: bool,
    has_anomaly: bool,
    signal_confidence: float,
) -> List[Dict]:
    catalog = actions_catalog() or [
        {"key": "notify_ops", "label": "Notify Operations", "weight": 0.97},
        {"key": "assign_field_officer", "label": "Assign Field Officer", "weight": 0.94},
        {"key": "request_cash_support", "label": "Request Provider Liquidity Support", "weight": 0.90},
        {"key": "monitor", "label": "Monitor", "weight": 0.72},
        {"key": "risk_review", "label": "Risk Review", "weight": 0.68},
        {"key": "data_quality_followup", "label": "Follow up with provider feed", "weight": 0.55},
    ]
    by_key = {a["key"]: a for a in catalog}

    selected: List[str] = []

    if signal_confidence < 0.5:
        selected += ["monitor"]
    else:
        if has_liquidity:
            if severity in ("high", "critical"):
                selected += ["notify_ops", "request_cash_support", "assign_field_officer"]
            else:
                selected += ["notify_ops", "monitor"]
        if has_anomaly:
            selected += ["risk_review", "monitor"]
        if not has_liquidity and not has_anomaly:
            selected += ["monitor"]

    if initial_owner == "data-quality" or data_quality < 0.6:
        selected.append("data_quality_followup")

    # De-duplicate, preserve order, attach weights from the catalog
    seen = set()
    out: List[Dict] = []
    for k in selected:
        if k in seen:
            continue
        seen.add(k)
        if k in by_key:
            out.append(dict(by_key[k]))
    # If for some reason the catalog didn't have anything, fall back to monitor
    return out or [{"key": "monitor", "label": "Monitor", "weight": 0.5}]


def _owner_for(initial_owner: str, severity: str) -> Tuple[str, str]:
    """Maps initial_owner → (role, human label) per the Ownership Engine table."""
    if initial_owner == "data-quality":
        return "provider", "Financial Service Provider — feed owner"
    if initial_owner == "anomaly":
        # Operations performs the initial evidence/context triage. It may then
        # escalate, but it must never make the independent compliance decision.
        return "ops", "Provider Operations / Network Coordination — initial triage"
    # liquidity
    if severity == "critical":
        return "ops", "Provider Operations / Network Coordination"
    return "ops", "Provider Operations / Network Coordination (initial triage)"


def _fused_explanation(
    inp: FusionInput,
    *,
    urgency: float,
    severity: str,
    priority_score: int,
    data_quality: float,
    confidence: float,
    action_hint: str,
) -> str:
    pieces: List[str] = []
    if inp.forecast_hours_to_shortage is not None:
        mins = max(0, int(round(inp.forecast_hours_to_shortage * 60)))
        pieces.append(
            f"Predicted {inp.provider.upper()} balance depletion in {mins} minutes"
            if mins < 60
            else f"Predicted {inp.provider.upper()} balance depletion in {inp.forecast_hours_to_shortage:.1f} hours"
        )
    if inp.anomaly_confidence >= 0.5:
        pieces.append("Unusual-activity rule evidence requires human review; it is not proof of wrongdoing")
    if inp.customers_waiting:
        pieces.append(f"approximately {inp.customers_waiting} customers could be affected")
    if inp.festival or inp.peak_hour:
        flag_bits = []
        if inp.festival:
            flag_bits.append("festival window")
        if inp.peak_hour:
            flag_bits.append("peak hour")
        pieces.append("Context: " + " and ".join(flag_bits))
    if data_quality < 0.7:
        pieces.append(
            f"provider feed data quality is degraded (score {data_quality:.2f}) — confidence reduced accordingly"
        )

    headline = {
        "critical": "Critical pressure detected.",
        "high": "High pressure detected.",
        "low": "Advisory signal detected.",
        "normal": "All providers within normal range.",
    }[severity]

    confidence_pct = int(round(confidence * 100))

    body = ". ".join(pieces)
    return (
        f"{headline} {body}. "
        f"Priority score: {priority_score}/100. "
        f"Confidence: {confidence_pct}%. "
        f"Recommended next step: {action_hint}. "
        f"Human review is required before any further action."
    ).strip()


def fuse(inp: FusionInput) -> FusionOutput:
    """The single entry point that the rest of the system calls."""
    w = _normalize_weights(weights_for(inp.provider))

    urgency = _urgency_from_forecast(inp.forecast_hours_to_shortage)
    impact = max(0.0, min(1.0, inp.customer_impact))
    confidence = max(0.0, min(1.0, inp.forecast_confidence))
    anomaly = max(0.0, min(1.0, inp.anomaly_confidence))
    dq = max(0.0, min(1.0, inp.data_quality))

    has_liquidity = inp.forecast_hours_to_shortage is not None
    has_anomaly = anomaly >= 0.5
    relevant_confidences = ([confidence] if has_liquidity else []) + ([anomaly] if has_anomaly else [])
    signal_confidence = sum(relevant_confidences) / len(relevant_confidences) if relevant_confidences else max(confidence, anomaly)
    supported_impact = impact * signal_confidence

    raw = (
        w["liquidityForecastWeight"] * urgency * confidence
        + w["anomalyWeight"] * anomaly
        + w["customerImpactWeight"] * supported_impact
        + w["confidenceWeight"] * signal_confidence
        + w["dataQualityWeight"] * (1.0 - dq)
    )
    raw += _severity_modifier(inp.initial_owner, inp.festival, inp.peak_hour)
    raw = max(0.0, min(1.0, raw))
    priority_score = int(round(raw * 100))
    if has_anomaly:
        # A rule-backed unusual-activity event must reach the human-review
        # queue even when no liquidity pressure accompanies it.
        priority_score = max(31, priority_score)
    # A shaky but operationally relevant signal becomes an explicit monitor
    # alert, never a confident high-impact recommendation.
    if (has_liquidity or anomaly > 0) and signal_confidence < 0.5:
        priority_score = max(31, min(priority_score, 60))
    severity = tier_for(priority_score)

    owner_role, owner_label = _owner_for(inp.initial_owner, severity)
    ranked = _pick_actions(
        severity=severity, initial_owner=inp.initial_owner, data_quality=dq,
        has_liquidity=has_liquidity, has_anomaly=has_anomaly,
        signal_confidence=signal_confidence,
    )

    fused_conf = max(0.0, min(0.99, signal_confidence * (0.5 + 0.5 * dq)))

    # Reasons must always be derived from data actually present.
    reasons: List[str] = []
    if urgency > 0:
        if inp.forecast_hours_to_shortage is not None and inp.forecast_hours_to_shortage < 1:
            reasons.append(f"Forecast: ~{int(inp.forecast_hours_to_shortage*60)} min to {inp.provider} shortage")
        else:
            reasons.append(f"Forecast: {inp.forecast_hours_to_shortage:.1f}h to {inp.provider} shortage")
    for r in inp.forecast_reasons[:3]:
        reasons.append(f"Forecast signal: {r}")
    if anomaly >= 0.5:
        for r in inp.anomaly_reasons[:3]:
            reasons.append(f"Anomaly signal: {r}")
    if dq < 0.7:
        reasons.append(f"Data quality degraded ({dq:.2f})")
    reasons.append(f"Decision-support uncertainty: {int(round(fused_conf * 100))}% confidence; human verification required")

    top_action = ranked[0]["label"] if ranked else "Monitor"

    explanation = _fused_explanation(
        inp, urgency=urgency, severity=severity, priority_score=priority_score,
        data_quality=dq, confidence=fused_conf, action_hint=top_action,
    )

    return FusionOutput(
        priority_score=priority_score,
        severity=severity,
        confidence=fused_conf,
        fused_explanation=explanation,
        ranked_actions=ranked,
        owner_role=owner_role,
        owner_label=owner_label,
        reasons=reasons,
    )
