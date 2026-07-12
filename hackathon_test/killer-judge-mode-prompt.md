# Killer Prompt: Judge-Mode Adversarial Testing Suite

Paste this into your AI coding tool (Codex CLI, Claude Code, etc.) **after** your base `testing-scripts-prompt.md` suite is already running. That suite proves the happy path works and produces your required validation metrics. This one exists to break things — it's built to find everything a strict judge would find first, so you find it first instead.

Uses the same `API_TOKEN` / environment setup as your base suite (see `TESTING.md`) — no new secrets needed.

---

## Patched in this version
Two rounds of external gap-analysis against the full problem statement, folded in:

**Round 1:**
1. **Intra-role provider leakage** — same job title (e.g., Ops), different provider, still must not share data. Part 1, Test 2.
2. **Shortage lead-time measurement** — the base suite now measures how *early* the alert fires, not just how accurate the number is. `testing-scripts-prompt.md` Layer 1.
3. **Localization fallback** — Bengali/Banglish rendering failure must fall back to safe English with all fields intact, not crash or blank out. Part 2, UX & Explainability.
4. **Math-contradiction trap** — a provider's own balance and its own ledger can contradict *themselves*, not just disagree with another feed. Part 1, Test 6.

**Round 2:**
5. **Bulk/export leak variant** — single-record IDOR tests don't cover list/export endpoints. Part 1, Test 2.
6. **Fairness / disparate-impact hunt** — new Part 1, Test 7.
7. **Ownership race condition** — the specific claim/assign race, sharper than the general concurrency test. New Part 1, Test 8.
8. **Stale-recommendation revalidation** — Part 2, Technical implementation.
9. **Demo-day auth/session robustness** — token expiry and session longevity added to the demo-chaos script, Part 2, Presentation.

---

## MINDSET INSTRUCTION FOR THE AI TOOL

You are now acting as the strictest judge on this hackathon's panel — the one who reads Section 14 ("Constraints and Guardrails") line by line and treats a quiet violation as worse than a missing feature, and who pokes at a live demo instead of watching it passively. Your job is not to confirm the system works. It's to find every way it doesn't, and write a script that proves it either way. Treat "it probably handles that" as false until a test confirms it.

---

## PART 1 — Guardrail violation hunting (maps to Section 14 — treat failures here as CRITICAL, not normal bugs)

Each test below actively tries to trigger a rule violation and asserts it does NOT happen. Any failure gets flagged separately in the final report as **"CRITICAL — DISQUALIFICATION RISK,"** not folded into the general pass rate.

1. **Fraud-declaration hunt** — Scan every API response, UI string, and log line the system can produce for words like "fraud," "confirmed," "guilty." Feed the anomaly engine its most extreme synthetic pattern and assert the output never crosses from "unusual / requires review" into a final determination (Section 7's mandatory language rule).
2. **Cross-provider leak/merge hunt** — Authenticated as a user scoped to Provider A, attempt IDOR-style parameter tampering (swap `provider_id`, omit provider filters, hit aggregate endpoints directly) to see if Provider B's raw balance or transaction data can be retrieved, inferred, or merged. Assert every attempt is blocked or correctly scoped.
   - **Sneaky variant — same role, different provider:** the one most teams miss. Log in as an Ops user at Provider A and, using the *normal* Ops workflow (not raw parameter tampering — just clicking through as intended), try to view or act on an active case/alert owned by Provider B's Ops team. Same job title, same permission level, wrong provider. Assert a `403`. Identical roles across providers still don't share a queue.
   - **Bulk/export variant:** hit every "list all," export, or summary/reporting endpoint specifically — these get built later and tested less than single-record lookups, and are the easiest place for a cross-provider leak to slip through unnoticed.
3. **Unauthorized-action hunt** — Call every coordination endpoint (assign/escalate/acknowledge/resolve) with parameters that would cause an automatic block, freeze, or fund movement. Assert the system only ever records a recommendation — never executes anything financial.
4. **Credential-collection hunt** — Scan every form schema, DB model, and request/response body for any field shaped like a PIN, OTP, password, or private key. Fail the build if one exists anywhere, even unused.
5. **Real-integration hunt** — Mock the entire network layer and log every outbound call during a full test run. Assert 100% of "provider" calls hit your simulated layer — zero calls to any real bKash/Nagad/Rocket endpoint, ever.
6. **Silent-confidence hunt** — Feed the system late, missing, and mutually-conflicting provider data (each separately, then combined). Assert it never returns a normal-confidence unified number in any of these states — it must visibly degrade, flag, or refuse.
   - **Concrete trap to include:** a fixture where a provider's own stated balance and its own ledger contradict each other — e.g., balance = 5,000 BDT, but a ledger entry deducts 10,000 BDT from that same stream. Not two sources disagreeing — one source disagreeing with itself. Assert `confidence_score` is forced to 0 and the UI shows an explicit data-invalid warning instead of any liquidity number.
7. **Fairness / disparate-impact hunt** — Section 8 explicitly names "avoid unsupported profiling" as a non-functional requirement. Bucket your anomaly engine's flag rate and false-positive rate by synthetic agent segment, area, or provider across a large batch of simulated transactions. Assert the rate doesn't diverge sharply across segments without a documented, legitimate risk-factor explanation — an engine that flags one simulated area or agent type at a visibly higher rate for no stated reason is exactly the "unsupported profiling" the brief tells you to avoid.
8. **Ownership race-condition hunt** — Fire two (or more) simultaneous "claim this alert" requests from different Ops users against the *same* alert. Assert exactly one wins cleanly and the other gets a clear "already claimed by X" response — not a silent overwrite, not a duplicate case, not a corrupted ownership field. This is the sharper, specific version of the general concurrency test in Part 2 — ownership assignment is the one place a race condition directly breaks the accountability the whole coordination workflow depends on.

---

## PART 2 — Rubric-weighted adversarial suite
*(mapped to Section 13's exact weights, so you can see where points are actually won or lost)*

### Technical implementation & integration quality — 25% (hit this hardest, it's the biggest single line item)
- **Fuzz every endpoint**: missing fields, wrong types, oversized payloads (10MB+ JSON), unicode/emoji in name fields, negative/zero/absurd transaction amounts, duplicate transaction IDs sent twice, out-of-order or future-dated timestamps.
- **Concurrency test**: fire 50 simultaneous requests touching the same agent's balance or the same alert record. Assert no lost updates, no duplicate alerts, no corrupted state.
- **Chaos test**: kill the DB connection or a simulated provider feed mid-request. Assert a graceful, explained error — never a crash, never a silently-wrong number.
- **Idempotency test**: replay an identical transaction/webhook twice. Assert it is not double-counted.
- **Load-spike test**, modeled directly on the brief's own scenario (Section 2, "afternoon before Eid"): simulate a documented concurrent-agent count and cash-out request rate; report whether latency/error rate holds within your stated bounds.
- **Stale-recommendation revalidation:** generate a recommendation (e.g., "top up bKash"), then change the underlying agent/provider state before the user acts on it. Assert the system either re-validates and updates the recommendation or visibly marks it stale — never lets a human act on a suggestion computed from data that's since changed underneath it.

### Data and analytical quality — 20%
- **Structuring/evasion test**: Section 9 explicitly names "transaction splitting" as a valid pattern to explore — so generate synthetic structured transactions (one large amount broken into several just-under-threshold pieces) and test whether detection still catches it. This is anomaly-detection QA against your own synthetic dataset, nothing external.
- **Push-past-comfortable false positives**: extend your existing salary-day/Eid normal-spike test set further (bigger legitimate merchant volume, longer legitimate holiday windows) to find where false positives actually start appearing — not just confirm they don't at your default test size.
- **Confidence calibration check**: bucket predictions by stated confidence (e.g., "70–80% confident") and verify actual accuracy in that bucket is close to 70–80% across many synthetic trials — directionally right isn't enough, the number itself has to mean something.

### Problem understanding & ecosystem relevance — 15%
- **Language audit**: scan all user-facing text for any phrasing implying one provider can see/control another provider's data or balance (explicitly forbidden), and confirm the "unusual"/"requires review" language rule (Section 7) is used everywhere a flag is shown.

### UX and explainability — 10%
- **Explanation coverage sweep**: walk every alert your test dataset produces and assert 100% include a stated reason, evidence, and confidence/uncertainty value. Any gap is a failure, not a rounding error.
- **Dead-end sweep**: script a walkthrough of every screen state reachable from the main flow; assert none surface a raw error, `undefined`, or an unexplained number to the user.
- **Localization fallback test**: deliberately break the Bengali/Banglish rendering path — corrupt the localization dictionary or mock a translation timeout — while an alert is actively rendering. Assert the system falls back to safe English copy that still contains the full reason/evidence/uncertainty/next-step fields, instead of throwing a raw error, showing a blank string, or printing `undefined`. This is the failure-mode counterpart to the happy-path Bengali test in the base suite's Layer 8.

### Security, privacy, fairness, responsible design — 5% (functionally pass/fail despite the low weight)
- Re-report Part 1's guardrail results explicitly under this category in the scorecard — this is literally what this rubric row grades.

### Presentation and demonstration — 5%
- **Demo-chaos script**: simulate what the live judged demo will actually hit — a brief network delay, two rapid clicks, a mid-flow refresh, a killed/restarted backend connection. Assert the UI recovers without losing the story you're telling.
- **API token expiry mid-demo:** simulate the auth token expiring while the dashboard is open. Assert a clear re-auth prompt, not a silent failure or a raw 401 in the console — this is the failure mode most likely to actually happen live, in front of judges, if your demo runs long.
- **Session longevity:** if practical, leave the dashboard open and idle for an extended period (or fast-forward simulated time) and confirm it's still responsive afterward — a judge circling back to your table later shouldn't find a frozen tab.

---

## PART 3 — Output: Judge Scorecard

Generate a report mapped 1:1 to Section 13's categories, plus a separate critical section for Part 1:

| Rubric category | Weight | Tests run | Passed | Points at risk |
|---|---|---|---|---|
| Problem understanding & ecosystem relevance | 15% | | | |
| Innovation & decision value | 20% | | | |
| Technical implementation & integration quality | 25% | | | |
| Data & analytical quality | 20% | | | |
| UX & explainability | 10% | | | |
| Security, privacy, fairness, responsible design | 5% | | | |
| Presentation & demonstration | 5% | | | |

**CRITICAL — Guardrail violations (Section 14):** *(list every Part 1 failure here separately — these risk disqualification, not just a lower score)*

---

## GUARDRAILS FOR THE AI TOOL WHILE GENERATING THIS

- Every adversarial input, structuring pattern, or fuzz payload you generate runs only against your own local synthetic test database — never against a real provider API, real credentials, or anything outside your own sandboxed test environment.
- This suite is additive — do not weaken, skip, or remove anything in the base `testing-scripts-prompt.md` suite to make these tests pass.
- If a test reveals a genuine guardrail violation, do not quietly patch it by loosening the test's assertion — flag it clearly so it gets fixed at the source.

---

**Now: run this after the base suite passes, and give me the Judge Scorecard — I want to see exactly where I'd lose points before the actual judges do.**
