# Master Prompt: Testing Suite for Super Agent Liquidity & Risk Intelligence Platform

Paste this entire prompt into your AI coding tool (Claude Code, Cursor, etc.) inside your project repo. It's written to generate a complete, judge-ready testing suite for the bKash SUST CSE Carnival 2026 Codex Community Hackathon problem statement.

---

## CONTEXT — Read this first

I'm building a prototype for the "Super Agent Liquidity & Risk Intelligence Platform" hackathon challenge. The product unifies multi-provider (bKash/Nagad/Rocket-style, simulated) liquidity visibility for an agent, forecasts shortages, flags unusual transaction activity, and drives a human-review coordination workflow (alert ownership, escalation, acknowledgement, resolution).

**Judges score 25% on "Technical implementation and integration quality"** (end-to-end completeness, component integration, alert routing/case workflow, robustness, reliability, demonstrable engineering depth) and **20% on "Data and analytical quality"** (validation method, uncertainty handling, anomaly evidence, false-positive awareness). Testing scripts are how we prove both. This is not optional polish — it is a primary grading criterion.

The required deliverable "Validation evidence" must show **at least three measured metrics** covering analytics, system performance, or reliability. Your job is to build the scripts that produce those metrics automatically and repeatably.

**Hard constraints you must respect in every test you write** (from the official rules — do not violate these even in test/mock scenarios):
- Only synthetic/mock/anonymized data — never real credentials, PINs, OTPs, or customer identities.
- Never test or simulate real fund transfers, wallet merges, or cross-provider settlement.
- Anomaly outputs must never be asserted/labeled as "fraud" — only "unusual" / "requires review."
- Every high-impact alert test must verify the alert exposes: reason, evidence, and uncertainty/confidence.
- Every provider-data-degradation test (missing/late/conflicting) must verify the system falls back to reduced confidence, not silent false confidence.

---

## MY STACK / ENVIRONMENT

> Fill in whatever's accurate — example below assumes a typical setup. Adjust before running.

- Backend: Django REST Framework (Python)
- Frontend: React + Vite
- Database: PostgreSQL
- Test runner (backend): `pytest` + `pytest-django` + `factory_boy` for synthetic data
- Test runner (frontend): `Vitest` / `Jest` + `React Testing Library`
- API load/perf testing: `locust` or `k6`
- API base URL: `http://localhost:8000/api/` (or your deployed staging URL)

**API Authentication:**
```
API_TOKEN=<YOUR_API_TOKEN_HERE>
```
Store this in a `.env.test` file (already in `.gitignore`, not committed) and load it via `os.environ["API_TOKEN"]` in the test config — never hardcode it in scripts, never print it in logs or test output, never commit it. If a test needs to hit protected endpoints, pull the token from environment at runtime only.

---

## WHAT I NEED YOU TO BUILD

Generate a **complete, runnable testing suite** organized into the following layers. For each layer, write actual test files (not just descriptions), a short README explaining how to run them, and a script that aggregates results into the metrics table the hackathon requires.

### 1. Unit tests — Core liquidity engine
- Test the aggregation logic that combines shared physical cash + per-provider e-money balances into a unified view, without ever merging provider identities in the underlying data model (assert providers stay logically separate in the data structure itself, not just in the UI).
- Test the shortage-forecast function against known synthetic time-series (e.g., linear/burst demand curves) and assert the "time until shortage" estimate falls within an acceptable error band.
- **Shortage detection lead-time measurement (this is a different test from the one above — accuracy of the estimate isn't the same as earliness of the warning):** feed the forecasting engine a simulated high-velocity drain curve modeled on the brief's own pre-Eid rush scenario. Mathematically verify the alert fires at least X hours before the balance would actually hit zero at the observed drain rate, and pipe that measured X directly into `metrics_report.json` under "Shortage detection lead time." You need both tests — one proves the number is right, the other proves the warning comes early enough to be useful.
- Test edge cases: zero balance, negative/invalid input rejection, exactly-at-threshold balance, simultaneous multi-provider shortage.
- Test that confidence/uncertainty score changes appropriately when input data is thin (few data points) vs. rich.
- **Provider-level demand/balance error (its own required metric — Section 12, row 1 — currently nothing produces it):** run the forecasting engine against a held-out slice of your synthetic dataset it wasn't tuned on, compute the error between predicted and actual balance/demand per provider, and report it as a single number (mean absolute error in BDT, or % error) into `metrics_report.json`. The hackathon's own suggested method is "validation on held-out simulated provider scenarios" — follow that exact approach so the number is defensible if a judge asks how you got it.

### 2. Anomaly detection validation
- Build a labeled synthetic dataset generator: normal transactions, plus injected "unusual" patterns (near-identical repeated amounts, velocity spikes, transaction splitting, circular activity — pick whichever pattern(s) your prototype implements).
- Write a script that runs detection against this labeled set and computes **precision, recall, F1, and false-positive rate**, and writes them to a JSON/CSV metrics file.
- Assert every anomaly result object includes: reason/evidence, confidence score, and a "requires review" label — and assert it never contains a "fraud" or "confirmed" field/string anywhere in the response.
- **Evidence completeness, not just presence:** tighten that assertion to check the evidence object specifically contains *time* and *area* fields — Section 7 names these as expected evidence dimensions, and a generic "evidence: true" check can pass while still missing what a reviewer actually needs.
- **Non-stub sanity check:** run the detector against several structurally different synthetic datasets (different volume, different injected pattern types) and assert outputs actually differ sensibly — flag counts, confidence scores, and reasons shouldn't be identical across clearly different inputs. This is the closest thing to an automated test for Section 7's mandatory "AI/analytics as a meaningful part of the product" requirement.

**Dedicated false-positive rate test (required metric — do not skip):**
Build a separate synthetic dataset that only contains *legitimate* high-volume patterns that could look suspicious but aren't: salary-disbursement days, and Eid/festival pre-holiday cash-out surges (large volume, tight time window, but organically distributed amounts/accounts rather than artificially repeated ones). Run the anomaly detector against this set and assert the number of incorrectly-flagged transactions stays under your documented threshold. Compute and report:

`False-positive rate = (normal-scenario transactions incorrectly flagged for review) / (total normal-scenario transactions)`

This must be reported as its own named metric — "Normal salary-day or Eid scenarios incorrectly flagged for review" — exactly matching the hackathon's own metrics table (Section 12) and the "False-positive rate" row required in Section 7 (Functional Expectations) and Section 16 (Submission Checklist: "Failure, uncertainty, and false-positive considerations shown"). Do not fold this into general precision/recall numbers — judges are looking for it as a distinct, explicit figure.

### 3. API / integration tests (use the API token here)
- Write authenticated integration tests against real API endpoints using `API_TOKEN` from environment.
- Cover: balance retrieval, alert creation, alert acknowledgement, alert escalation, case resolution, and the "who owns this alert" assignment endpoint.
- Assert response schemas strictly (status codes, required fields present, no leakage of another provider's raw internal data on a shared endpoint).
- **Same-role, different-provider scoping test:** authenticate as an Ops user tied to Provider A, and attempt to read or act on a case/alert owned by Provider B's Ops team — same role, same permission level, wrong provider. This must fail. Testing agent-vs-risk-analyst boundaries isn't enough on its own; two people with the *same* job title at different providers is the gap teams usually miss.
- **Bulk/aggregate endpoint scoping:** the single-record test above isn't enough on its own — separately hit any "list all agents," export, or aggregate/reporting endpoint and assert the response stays filtered to the caller's own provider. List endpoints get built later and tested less than single-record lookups, making them the easier place for a cross-provider leak to slip through.
- **Alert history endpoint:** explicit test for a dedicated "fetch this alert's full state history" (and/or "fetch this agent's alert timeline") endpoint — Section 7 recommends surfacing history as its own capability, separate from just proving the lifecycle got logged somewhere.
- Test auth failure paths: missing token, expired/invalid token, insufficient role/permission for an operations-only or risk-analyst-only action.
- Include contract tests that fail loudly if a response shape changes unexpectedly (helps demo stability under judges' eyes).

### 4. Data-quality / reliability & fallback tests
- Simulate delayed provider feed, missing provider feed, and conflicting balances (two sources disagree) — assert the system: (a) does not silently produce a confident unified number, (b) lowers/flags confidence, (c) surfaces a clear warning to the user, (d) still shows provider balances separately rather than a blended guess.
- Add chaos-style tests: randomly drop/delay a percentage of simulated provider events and assert the system degrades gracefully (no crash, no false-positive alert storm).
- **Deterministic math-contradiction fixture:** construct a fixture where a provider's stated balance and its own transaction ledger are mutually impossible — e.g., stated balance 5,000 BDT, but a ledger entry deducts 10,000 BDT from that same stream. This is different from "two feeds disagree" — it's one feed contradicting itself. Assert the system detects the arithmetic impossibility, forces `confidence_score` to 0 (or an equivalent explicit invalid-data flag), and shows a clear data-quality warning instead of any liquidity number — never silently averages it away into something plausible-looking.

### 5. Coordination / workflow tests (audit trail)
- End-to-end test of the full alert lifecycle: created → routed to correct stakeholder role → acknowledged → escalated (if applicable) → resolved, asserting each transition is timestamped and logged/traceable.
- Assert the system never auto-resolves, auto-blocks, or takes a financial action — only ever proposes/recommends and waits for human action.
- Test that a risk/compliance-only field (e.g., final determination) cannot be set by an "agent" role — role-boundary test.
- **Case notes:** a note can be added to a case by an authorized role, retrieved as part of that case's record, and persists across a status change (a note added while "Open" is still visible once "Escalated"). Named explicitly in both Section 4 (Optional) and Section 7 (Recommended).
- **Three-category visible distinction:** Section 4's Primary objectives require helping users tell apart operational demand spikes, data-quality problems, and patterns requiring review — not just detecting each internally. Assert these surface as genuinely distinct values (a `category`/`type` field, distinct label) rather than collapsing into one generic "alert" shape.
- **Case ownership is atomic, not just logged:** this suite tests that lifecycle transitions get logged (see above). The adversarial suite stress-tests *concurrent* ownership claims — see `killer-judge-mode-prompt.md` Part 1 for the race-condition version.

### 6. Explainability coverage test
- Script that scans all alerts generated by a full sample run and computes: **% of alerts with a populated reason + evidence + uncertainty field** ("Alert explanation coverage" — this is a required metric in the hackathon's own table). Target should be 100%; the script should fail CI if any alert lacks these fields.

### 7. Performance / latency tests
- Use `locust` or `k6` to hit the core alert-generation and balance-aggregation endpoints under a documented load (state the concurrent users / requests-per-second you test at).
- Report **average and p95/p99 latency** — this exactly matches the "API or processing latency" metric the judges expect ("Average and percentile timing at a documented agent or transaction volume").
- Include a baseline dataset size (e.g., "500 simulated transactions across 3 providers, 20 agents") so the number is reproducible and explainable in the demo.

### 8. Language output tests — Bengali AND Banglish are separate tests (if implemented)
- The brief lists Bengali, Banglish, and English as three distinct options (Section 7, Section 4 Secondary) — Banglish (Bengali written in Latin script) is a different code path from Bengali-script rendering and can break independently (transliteration bugs, mixed-script edge cases). Don't test one and assume the other works.
- **Bengali script test:** assert a Bengali-script alert contains situation description, evidence, uncertainty language, and a safe next step, matching the illustrative format in the brief.
- **Banglish test:** the same four-part assertion, run separately against the Banglish output path.
- This covers the happy path only, for both. What happens when localization *fails* (dictionary missing, translation service times out mid-render) is tested separately, deliberately, in `killer-judge-mode-prompt.md` Part 2 — see the Localization fallback test there.

### 9. Security/privacy smoke tests
- Assert no endpoint ever accepts or stores PIN/OTP/password-like fields.
- Assert all IDs in test fixtures are clearly synthetic (e.g., prefixed `SYN-` or similar) so reviewers can see nothing real was used.

### 10. Role-coverage functional tests (all 5 roles, not just permission boundaries)
Earlier layers test *who can't* see what. This layer tests *what each role's own view actually shows* — it's possible to pass every permission-boundary test while never having actually built half these views.
- **Agent:** dashboard response includes combined cash + per-provider balances, a shortage forecast with confidence, and their own case history — not an empty scaffold.
- **Ops:** triage queue is filterable by area/agent/time, and a claimed case shows ownership + recommended action. If you've implemented sub-levels of the field-officer → area-manager → central-ops hierarchy (Section 5), test that a field-officer-level user sees only their assigned agents while an area-manager-level user sees an aggregate across their area. The brief says "actual structures may be different," so this isn't mandatory — skip it rather than fake it if you haven't built sub-levels.
- **Financial Provider:** their monitoring view returns only their own outlets' data, never another provider's — including in aggregate/summary numbers, not just record-level lookups.
- **Risk Analytics:** their case view includes the full evidence chain (not a truncated summary), and confirm no endpoint available to this role can set a final block/freeze/fraud-confirmed state — check this is actually absent from the API, not just hidden in the UI.
- **Management:** aggregate dashboard reflects area-level risk and recurring problems (not a single-outlet view scaled up), and includes a "service continuity" style rollup metric — the closest testable proxy for the brief's Customers stakeholder need ("receive more reliable service"), since Customers aren't a system-authenticated role in this design and don't need their own test suite.

### 11. Data and documentation completeness checks
Lower-effort, high-value tests most teams skip because they don't look like "real" tests — several map directly to Required Deliverables (Section 10).
- **Fixture schema completeness:** assert your synthetic dataset generator actually produces every field Section 9 names — agent ID, provider ID, area, time, transaction type, amount, status, opening balance, current balance, event flags, case status. A generator quietly missing "area," for example, breaks several other tests upstream without anyone noticing.
- **Per-alert-type metadata registry:** Section 9's risk-interpretation rule requires documenting, for every anomaly type you detect, what the flag means and its expected false-positive risk. Enforce this in code: if your anomaly types live in a registry/enum/dict, assert every entry has a non-empty `description` and `expected_fp_rate` (or equivalent) — a new anomaly type literally can't ship without this metadata.
- **Responsible-design note completeness:** a lint-style check that your `RESPONSIBLE_DESIGN.md` (or equivalent required deliverable) contains sections covering privacy, human review, false positives, advisory boundaries, and what the system intentionally does not do — required per Section 10, and an easy, avoidable point loss if a section is missing.
- **Limitations documentation exists** and isn't a placeholder.

### 12. Conditional tests — only if you built the optional/advanced feature
Section 4's Secondary/Optional-advanced objectives and Section 15's Innovation Opportunities list features you are *not* required to build. Don't burn time writing tests for features that don't exist — if you implemented any of these, add the matching test; if not, skip it entirely rather than stub it out:
- **Multi-dimension prioritization** (area/provider/time/agent filtering): assert filtering actually narrows results correctly along whichever dimensions you built.
- **What-if scenario simulator:** assert a simulated demand shift/local event/agent-unavailability input produces a differently-shaped forecast than baseline, not a cosmetic-only change.
- **Nearby-agent support discovery:** assert the suggested agent is actually closer or genuinely surplus-balanced, not just the first record in the table.
- **Hotspot mapping:** assert the calculation responds to changes in underlying data (more concentrated pressure → ranking changes) rather than being static/decorative.
- **Cross-provider pattern insight / graph-based relationship insight / peer comparison:** assert these use only simulated identifiers and never resolve back to anything resembling a real cross-provider identity link.
- **Human-review feedback loop:** assert a reviewer's confirmed true/false-positive verdict is actually stored and retrievable, even if the model doesn't retrain on it for the hackathon.
- **Time-based auto-escalation:** assert an unacknowledged case escalates after your documented window, using a simulated clock — don't make the test wait in real time.

**One item intentionally left out:** Section 8 lists "Usability" as a non-functional area, but genuine usability validation (task-completion rate, time-to-understand) needs human participants — it isn't something a script can honestly produce. Don't fabricate an automated "usability test." Note it as a limitation in your final presentation instead — Section 17 explicitly rewards "honest treatment of limitations."

---

## OUTPUT FORMAT I WANT FROM YOU

1. Actual test files organized in a `/tests` directory matching my project structure (ask me for the real folder layout if you need it, or infer it from the repo).
2. A `run_all_tests.sh` (or `Makefile` target) that runs every layer above in sequence and fails fast with a clear summary.
3. A `metrics_report.json` (or `.md` table) auto-generated after the run, formatted to drop directly into the "Validation evidence" and "Success Criteria" sections of my final presentation — matching this table structure:

| Metric | Result | Method |
|---|---|---|
| Provider-level demand/balance error | | |
| Shortage detection lead time | | |
| Anomaly precision / recall / F1 | | |
| False-positive rate (normal salary-day/Eid scenarios incorrectly flagged) | | |
| Alert explanation coverage | | |
| API/processing latency (avg, p95) | | |
| Reliability under degraded input | | |

4. A short `TESTING.md` explaining how to set `API_TOKEN` locally, how to run each suite individually, and how to regenerate the metrics table before the final demo.

---

## GUARDRAILS FOR YOU (the AI coding tool) WHILE GENERATING THIS

- Do not invent fake "production-ready" or "regulatory-approved" language anywhere in test names, comments, or the report — the hackathon explicitly forbids claiming production fraud-detection readiness.
- Do not write any test that performs or mocks a real financial transaction, transfer, or wallet-balance write outside the sandboxed test DB.
- Keep provider data structurally separate throughout — no test fixture should merge two providers' balances into one record.
- If something about my repo structure, chosen frameworks, or anomaly pattern isn't clear from context, ask me directly before generating code rather than guessing silently.

---

**Now: inspect my current repo structure and existing code first, then generate the test suite layer by layer starting with #1 (unit tests for the liquidity engine), showing me each layer before moving to the next so I can course-correct early.**
