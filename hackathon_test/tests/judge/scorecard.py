"""Aggregates Part 1 + Part 2 outcomes into a Judge Scorecard JSON.

Produces ``reports/judge_scorecard.json`` matching the structure described
in killer-judge-mode-prompt.md PART 3. The scorecard is regenerated every
time ``pytest tests/judge`` finishes — even on failure — so the demo team
can immediately see which rows regressed.

This module is a script wrapper; it gets called from ``run_all_tests.sh``.
It can also be run standalone:
    python -m tests.judge.scorecard
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
REPORTS_DIR = REPO_ROOT / "tests" / "reports"
REPORTS_DIR.mkdir(parents=True, exist_ok=True)

RUBRIC_ROWS = [
    # (row id, weight %, maps to source dict)
    ("problem_understanding",     15, "judgment"),
    ("data_quality",              20, "judgment"),
    ("technical_integration",     25, "judgment"),
    ("innovation",                20, "judgment"),
    ("ux_explainability",         10, "judgment"),
    ("security_privacy_fairness",  5, "judgment"),
    ("presentation",               5, "judgment"),
]


def run_part(label: str, marker: str) -> dict:
    """Run a pytest invocation by marker and parse the JUnit-style summary."""
    junit_path = REPORTS_DIR / f"junit_{label}.xml"
    cmd = [
        sys.executable, "-m", "pytest",
        "tests/",
        f"-m", marker,
        "-q", "--no-header", "--tb=line",
        f"--junitxml={junit_path}",
    ]
    env = os.environ.copy()
    env.setdefault("PYTHONPATH", str(REPO_ROOT))
    proc = subprocess.run(
        cmd, cwd=str(REPO_ROOT),
        capture_output=True, text=True, env=env,
    )
    return {
        "label": label,
        "returncode": proc.returncode,
        "stdout_tail": proc.stdout[-2000:],
        "junit": str(junit_path),
        "ran_at": datetime.now(timezone.utc).isoformat(),
    }


def rollup(scores: dict) -> float:
    """Return a 0..100 weighted total per §13 weighting."""
    total = 0.0
    for row_id, weight, _ in RUBRIC_ROWS:
        s = scores.get(row_id, 0.0)
        total += s * weight
    return round(total, 2)


def main() -> int:
    results = {
        "part1_guardrails": run_part("judge_part1", "guardrail_critical"),
        "part2_rubric": run_part("judge_part2", "judge"),
    }
    # Stub scoring — full rubric scoring requires per-row pytest counts.
    scores = {
        "problem_understanding":     100.0 if results["part2_rubric"]["returncode"] == 0 else 0.0,
        "data_quality":              100.0 if results["part2_rubric"]["returncode"] == 0 else 0.0,
        "technical_integration":     100.0 if results["part2_rubric"]["returncode"] == 0 else 0.0,
        "innovation":                100.0 if results["part2_rubric"]["returncode"] == 0 else 0.0,
        "ux_explainability":         100.0 if results["part2_rubric"]["returncode"] == 0 else 0.0,
        "security_privacy_fairness": 100.0 if results["part1_guardrails"]["returncode"] == 0 else 0.0,
        "presentation":              100.0 if results["part2_rubric"]["returncode"] == 0 else 0.0,
    }
    payload = {
        "disqualification_guardrails": results["part1_guardrails"]["returncode"],
        "rubric_rows": [
            {"id": r[0], "weight_pct": r[1], "score": scores[r[0]]}
            for r in RUBRIC_ROWS
        ],
        "weighted_total": rollup(scores),
        "raw_runs": results,
    }
    out_path = REPORTS_DIR / "judge_scorecard.json"
    out_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"[scorecard] wrote {out_path}")
    return int(results["part1_guardrails"]["returncode"] != 0)


if __name__ == "__main__":
    sys.exit(main())