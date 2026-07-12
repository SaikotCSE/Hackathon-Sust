# TESTING — Super Agent Liquidity & Risk Intelligence Platform

This directory (`tests/`) contains the complete test suite for the
prototype. It is structured in two parts:

1. **9-layer base suite** (per `testing-scripts-prompt.md`) — verifies
   each functional capability against §4 objectives and §7 expectations.
2. **Killer-Judge suite** (`tests/judge/`) — adversarial probes per
   `killer-judge-mode-prompt.md` PART 1 (6 critical guardrail hunts) and
   PART 2 (6 rubric-weighted stress suites).

The runner writes three artifacts to `tests/reports/`:

| File                           | Contents                                      |
|--------------------------------|-----------------------------------------------|
| `metrics_report.json`          | Section 12's 7-row metrics table, machine-readable |
| `metrics_report.md`            | Same data + §13 scorecard, human-readable     |
| `judge_scorecard.json`         | PART 3 numeric roll-up                        |

---

## Quick start

```bash
# 1. Copy the template and fill in at least API_TOKEN + API_BASE_URL.
cp .env.test .env

# 2. (Optional) start the backend so layer 3 / 7 / judge run live API tests.

# 3. Run everything.
chmod +x run_all_tests.sh
./run_all_tests.sh
```

By default, integration tests are *skipped* if the backend at `API_BASE_URL`
is unreachable — but the python-level tests (Layer 1, 2, 5, 6, 8, 9 + Judge
literals) run unconditionally and still produce the Section 12 metrics.

---

## Layer-by-layer reference

| Layer | Marker             | Backend needed? | What it covers                          |
|-------|--------------------|-----------------|------------------------------------------|
| 1     | `layer1_unit`      | No              | Liquidity engine maths (Layer 1 tests)   |
| 2     | `layer2_anomaly`   | No              | Anomaly precision / recall / F1          |
| 3     | `layer3_api`       | Yes             | API contracts + role / provider scoping  |
| 4     | `layer4_data_quality` | No           | Missing / late / contradictory feeds     |
| 5     | `layer5_coordination` | No           | Alert lifecycle + audit trail             |
| 6     | `layer6_explainability` | No        | 100% reason / evidence / uncertainty     |
| 7     | `layer7_perf`      | Yes             | Locust burst, p95 latency                |
| 8     | `layer8_locale`    | No              | Bengali / Banglish alert rendering        |
| 9     | `layer9_security`  | No              | Sensitive-field rejection, synthetic IDs  |
| Judge | `judge`            | Mixed           | Disqualification guards + rubric stress  |

Run an individual layer:

```bash
python -m pytest tests/api -m layer3_api
python -m pytest tests/judge/part1_guardrails -m guardrail_critical -v
```

Run only the §12 metrics writers:

```bash
python -m tests.judge.scorecard
```

---

## Environment

The test suite reads from `.env` (or `.env.test` as fallback). Required:

| Var                          | Default                  | Purpose                            |
|------------------------------|--------------------------|------------------------------------|
| `API_BASE_URL`               | `http://localhost:8000/api/` | Backend root URL                |
| `API_TOKEN`                  | (required to run L3)     | Bearer token for `mgmt` role       |
| `ROLE_OPS_PROVIDERA_TOKEN`   | (optional)               | Token for `ops_providerA` role     |
| `ROLE_RISK_PROVIDERA_TOKEN`  | (optional)               | Token for `risk_providerA` role    |
| `LOCUST_USERS`               | 50                       | Concurrent Locust users            |
| `LOCUST_SPAWN_RATE`          | 5                        | Ramp-up rate                       |
| `LOCUST_RUN_TIME`            | 60s                      | Duration                           |
| `ANOMALY_FP_THRESHOLD`       | 0.05                     | Judge suite extended-FP threshold  |
| `EXPLANATION_COVERAGE_TARGET`| 1.0                      | Layer 6 target                     |

`.env` is gitignored. **Never commit a real `API_TOKEN` or provider
secret.** All sensitive payloads in test fixtures use `SYN-` prefixes and
synthetic names.

---

## What the Judge suite enforces (PART 1, 6 critical guardrails)

| # | File                                      | What it hunts                                          |
|---|-------------------------------------------|--------------------------------------------------------|
| 1 | `test_01_fraud_declaration_hunt.py`       | Forbidden accusation words (`fraud`, `confirmed`, …)    |
| 2 | `test_02_cross_provider_leak_hunt.py`     | IDOR + cross-tenant leaks                              |
| 3 | `test_03_unauthorized_action_hunt.py`     | Endpoints that execute, not recommend                  |
| 4 | `test_04_credential_collection_hunt.py`   | PIN/OTP/password field names anywhere                  |
| 5 | `test_05_real_integration_hunt.py`        | Real provider DNS / outbound calls                     |
| 6 | `test_06_silent_confidence_hunt.py`       | Confident numbers on stale/contradictory data          |

A failure in **any** of these is a Section 14 disqualification risk.

---

## What the Judge suite stress-tests (PART 2, rubric-weighted)

| § | File                                  | Rubric row % |
|---|---------------------------------------|--------------|
| 1 | `test_01_technical_integration.py`    | 25 %         |
| 2 | `test_02_data_quality.py`             | 20 %         |
| 3 | `test_03_problem_understanding.py`    | 15 %         |
| 4 | `test_04_ux_explainability.py`        | 10 %         |
| 5 | `test_05_security_privacy.py`         | 5 %          |
| 6 | `test_06_presentation.py`             | 5 %          |

Each file maps to a row of §13's weighting. Failures count against that
row's points-at-risk in `metrics_report.md`.

---

## Regenerating the Section 12 metrics table

After any test run, `tests/reports/metrics_report.md` is overwritten with
the live values. To regenerate without re-running tests:

```bash
python -m tests.judge.scorecard
```

The table exactly matches §12's seven rows:

1. Provider-level demand/balance error
2. Shortage detection lead time
3. Anomaly precision / recall / F1
4. False-positive rate (normal salary-day / Eid scenarios)
5. Alert explanation coverage
6. API / processing latency (avg, p95)
7. Reliability under degraded input

---

## Troubleshooting

* **`pytest.skip` on every API test** — backend at `API_BASE_URL` not
  reachable. Start it with `docker-compose up` / `python manage.py
  runserver` / equivalent, then re-run.
* **`AttributeError: 'APIClient' has no attribute 'token'`** — `.env`
  missing. Copy from `.env.test` and set `API_TOKEN`.
* **`forbidden token 'fraud' … in build/`** — a JS bundle accidentally
  serialized an intern string. Trim it (the Judge suite also runs in CI).
* **Judge PART 1 fails on a sample alert payload** — your sample fixtures
  use `fraud` somewhere. Replace with `unusual` / `requires review`.
