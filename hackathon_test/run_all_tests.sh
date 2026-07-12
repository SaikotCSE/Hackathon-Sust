#!/usr/bin/env bash
# run_all_tests.sh — execute every layer of the testing suite and emit the
# canonical Section 12 metrics + judge scorecard.
#
# Usage:
#   ./run_all_tests.sh                # full run (slow)
#   ./run_all_tests.sh --layer3 only  # single layer
#   ./run_all_tests.sh --no-judge     # skip the killer-judge suite
#
# Environment:
#   Copy .env.test to .env in the repo root, fill in API_TOKEN, and the
#   script reads it automatically. If no backend is reachable, integration
#   tests are skipped (unittest / anomaly / python-helper layers still run).
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$REPO_ROOT"

if [ -f .env ]; then
  set -a
  # shellcheck disable=SC1091
  . ./.env
  set +a
elif [ -f .env.test ]; then
  echo "[run_all_tests.sh] no .env found; loading defaults from .env.test"
  set -a
  # shellcheck disable=SC1091
  . ./.env.test
  set +a
fi

LAYER_ONLY=""
SKIP_JUDGE=0
SKIP_PERF=0
for arg in "$@"; do
  case "$arg" in
    --layer*)         LAYER_ONLY="$arg" ;;
    --no-judge)       SKIP_JUDGE=1 ;;
    --no-perf)        SKIP_PERF=1 ;;
    -h|--help)
      grep '^#' "$0" | sed 's/^# \{0,1\}//'
      exit 0
      ;;
    *) echo "unknown arg: $arg"; exit 2 ;;
  esac
done

mkdir -p tests/reports

run_pytest() {
  local label="$1"; shift
  echo
  echo "============================================================"
  echo "  $label"
  echo "============================================================"
  python -m pytest "$@" || echo "[run_all_tests.sh] $label returned non-zero"
}

# ----- Layers 1, 2, 5, 6, 8, 9 — pure-python tests, no backend needed -----
run_pytest "Layer 1 — Liquidity engine unit tests"      tests/unit -m layer1_unit
run_pytest "Layer 2 — Anomaly detection"               tests/anomalies -m layer2_anomaly
run_pytest "Layer 5 — Coordination / lifecycle"        tests/coordination -m layer5_coordination
run_pytest "Layer 6 — Explanation coverage"            tests/explainability -m layer6_explainability
run_pytest "Layer 8 — Localization"                    tests/locale -m layer8_locale
run_pytest "Layer 9 — Security smoke"                  tests/security -m layer9_security

# ----- Layer 3 — API integration (skipped automatically if no backend) ----
run_pytest "Layer 3 — API endpoints"                   tests/api -m layer3_api
run_pytest "Layer 3 — Role / provider scoping"         tests/api/test_role_provider_scoping.py

# ----- Layer 4 — Data quality / fallback -------------------------------
run_pytest "Layer 4 — Data quality / fallback"         tests/data_quality -m layer4_data_quality

# ----- Layer 7 — Performance (optional) -------------------------------
if [ "$SKIP_PERF" = 0 ]; then
  run_pytest "Layer 7 — Performance / Locust"             tests/perf -m layer7_perf
else
  echo "[run_all_tests.sh] skipping Layer 7 (--no-perf)"
fi

# ----- Judge PART 1 — CRITICAL guardrail hunt ---------------------------
# A failure here is a Section 14 / PART 1 disqualification risk.
if [ "$SKIP_JUDGE" = 0 ]; then
  if ! run_pytest "Judge PART 1 — Guardrails (DISQUALIFICATION RISK)" \
        tests/judge/part1_guardrails -m guardrail_critical; then
    echo
    echo "================================================================="
    echo "  CRITICAL: a Section-14 guardrail test failed."
    echo "  Read tests/reports/judge_scorecard.md and address immediately."
    echo "================================================================="
  fi

  run_pytest "Judge PART 2 — Rubric-weighted adversarial tests" \
        tests/judge/part2_rubric -m judge
fi

# ----- Scorecard -------------------------------------------------------
echo
echo "------------------------------------------------------------"
echo "  Aggregating Judge Scorecard"
echo "------------------------------------------------------------"
python -m tests.judge.scorecard || true

echo
echo "------------------------------------------------------------"
echo "  Artifacts written to tests/reports/"
echo "    - metrics_report.json     (Section 12, all layers)"
echo "    - metrics_report.md       (Section 12 + §13 scorecard)"
echo "    - judge_scorecard.json    (PART 3 numeric roll-up)"
echo "------------------------------------------------------------"
