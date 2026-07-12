"""Aggregated metrics writer.

Every layer pushes structured numbers into the shared sink. After the run, the
sink is dumped to ``tests/reports/metrics_report.json`` matching Section 12's
table, plus a ``tests/reports/judge_scorecard.md`` mirroring Section 13.

The artifact is also written as a Markdown table so it can be pasted directly
into the "Validation evidence" slide of the final presentation.
"""
from __future__ import annotations

import json
import os
import statistics
import time
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional

from .api_client import CallRecord
from .env import CONFIG

# Required metrics from problem_statement.md §12.
SECTION_12_METRICS: List[Dict[str, str]] = [
    {"key": "provider_demand_balance_error",
     "label": "Provider-level demand/balance error"},
    {"key": "shortage_detection_lead_time",
     "label": "Shortage detection lead time"},
    {"key": "anomaly_precision_recall_f1",
     "label": "Anomaly precision / recall / F1"},
    {"key": "false_positive_rate_normal_spikes",
     "label": "False-positive rate (normal salary-day/Eid scenarios incorrectly flagged)"},
    {"key": "alert_explanation_coverage",
     "label": "Alert explanation coverage"},
    {"key": "api_processing_latency",
     "label": "API/processing latency (avg, p95)"},
    {"key": "reliability_under_degraded_input",
     "label": "Reliability under degraded input"},
]


# Rubric categories from problem_statement.md §13 (weights sum to 100%).
SECTION_13_CATEGORIES: List[Dict[str, Any]] = [
    {"key": "problem_understanding",         "label": "Problem understanding & ecosystem relevance",          "weight": 15},
    {"key": "innovation_decision_value",     "label": "Innovation & decision value",                          "weight": 20},
    {"key": "technical_implementation",      "label": "Technical implementation & integration quality",       "weight": 25},
    {"key": "data_analytical_quality",       "label": "Data & analytical quality",                            "weight": 20},
    {"key": "ux_explainability",             "label": "UX & explainability",                                  "weight": 10},
    {"key": "security_privacy_fairness",     "label": "Security, privacy, fairness, responsible design",        "weight": 5},
    {"key": "presentation_demonstration",    "label": "Presentation & demonstration",                         "weight": 5},
]


@dataclass
class MetricsSink:
    metrics: Dict[str, Any] = field(default_factory=dict)
    judge_runs: Dict[str, int] = field(default_factory=dict)
    judge_passes: Dict[str, int] = field(default_factory=dict)
    critical_violations: List[Dict[str, Any]] = field(default_factory=list)
    api_calls: List[CallRecord] = field(default_factory=list)
    started_at: float = field(default_factory=lambda: time.time())

    # ------------- metric setters ---------------------------------------------
    def set(self, key: str, value: Any, method: str = "") -> None:
        self.metrics[key] = {"value": value, "method": method}

    def add_critical(self, label: str, detail: str) -> None:
        self.critical_violations.append({"label": label, "detail": detail})

    def record_calls(self, calls: Iterable[CallRecord]) -> None:
        self.api_calls.extend(calls)

    # ------------- judge-side rollups ----------------------------------------
    def tally(self, category: str, passed: bool) -> None:
        self.judge_runs[category] = self.judge_runs.get(category, 0) + 1
        if passed:
            self.judge_passes[category] = self.judge_passes.get(category, 0) + 1

    # ------------- outputs ----------------------------------------------------
    def latency_summary(self) -> Dict[str, float]:
        # Calls are pulled in via ``record_calls``; if a layer forgot, it's
        # tracked elsewhere too (via APIClient.call_log on a per-instance basis).
        # We dedupe by constructing a flat list of durations.
        if self.api_calls:
            durations = sorted(c.duration_ms for c in self.api_calls)
        else:
            durations = []
        if not durations:
            return {"avg_ms": 0.0, "p50_ms": 0.0, "p95_ms": 0.0, "p99_ms": 0.0, "n": 0}
        n = len(durations)

        def pct(p: float) -> float:
            idx = max(0, min(n - 1, int(round((p / 100) * (n - 1)))))
            return durations[idx]

        return {
            "avg_ms": round(statistics.fmean(durations), 2),
            "p50_ms": pct(50),
            "p95_ms": pct(95),
            "p99_ms": pct(99),
            "n": n,
        }

    def write(self) -> Dict[str, str]:
        # Always pull latencies from recorded calls before writing.
        lat = self.latency_summary()
        if lat["n"] > 0 and "api_processing_latency" not in self.metrics:
            self.metrics["api_processing_latency"] = {
                "value": {
                    "avg_ms": lat["avg_ms"],
                    "p50_ms": lat["p50_ms"],
                    "p95_ms": lat["p95_ms"],
                    "p99_ms": lat["p99_ms"],
                    "n": lat["n"],
                },
                "method": "Locust + APIClient.call_log dur聚合",
            }

        out_json = CONFIG.reports_dir / "metrics_report.json"
        with out_json.open("w", encoding="utf-8") as fh:
            json.dump(
                {
                    "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                    "metrics": self.metrics,
                    "judge_runs_by_category": self.judge_runs,
                    "judge_passes_by_category": self.judge_passes,
                    "critical_violations": self.critical_violations,
                },
                fh,
                indent=2,
                ensure_ascii=False,
            )

        out_md = CONFIG.reports_dir / "metrics_report.md"
        with out_md.open("w", encoding="utf-8") as fh:
            fh.write("# Validation evidence (Section 12)\n\n")
            fh.write("| Metric | Result | Method |\n|---|---|---|\n")
            for spec in SECTION_12_METRICS:
                entry = self.metrics.get(spec["key"], {})
                value = entry.get("value", "")
                method = entry.get("method", "")
                fh.write(f"| {spec['label']} | {value} | {method} |\n")

            fh.write("\n# Judge scorecard (Section 13)\n\n")
            fh.write("| Rubric category | Weight | Tests run | Passed | Points at risk |\n|---|---|---|---|---|\n")
            for spec in SECTION_13_CATEGORIES:
                run = self.judge_runs.get(spec["key"], 0)
                ok = self.judge_passes.get(spec["key"], 0)
                # At-risk points are the failed-share of the category weight.
                at_risk = 0.0
                if run > 0:
                    at_risk = round(spec["weight"] * (1 - ok / run), 2)
                fh.write(
                    f"| {spec['label']} | {spec['weight']}% | {run} | {ok} | {at_risk}% |\n"
                )

            if self.critical_violations:
                fh.write("\n## CRITICAL — Guardrail violations (Section 14)\n\n")
                for v in self.critical_violations:
                    fh.write(f"- **{v['label']}** — {v['detail']}\n")
            else:
                fh.write("\n## CRITICAL — Guardrail violations (Section 14)\n\n_None flagged._\n")

        # Also emit a judge_scorecard.md alias expected by PART 3.
        scorecard_md = CONFIG.reports_dir / "judge_scorecard.md"
        if scorecard_md.resolve() != out_md.resolve():
            scorecard_md.write_text(out_md.read_text(encoding="utf-8"), encoding="utf-8")

        return {"json": str(out_json), "md": str(out_md)}


# Module-level singleton — pytest fixtures reference this.
SINK = MetricsSink()


def record_metric(key: str, value, method: str = "") -> None:
    """Module-level convenience wrapper around :data:`SINK`.

    Tests that import this helper via ``from tests.helpers.metrics import record_metric``
    (rather than receiving it as a pytest fixture) need a top-level symbol that
    forwards to the singleton sink. Behaviour matches the conftest fixture of
    the same name.
    """
    SINK.set(key, value, method)


__all__ = [
    "MetricsSink",
    "MetricRecord",
    "SINK",
    "record_metric",
] + [
    name for name in globals() if name.isupper() and not name.startswith("_")
]  # keep all UPPER_CASE constants exported.
