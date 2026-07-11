# MASTER BUILD PROMPT
## AI-Powered Super Agent Liquidity & Risk Intelligence Platform

> **How to use this document:** Paste the sections below into an AI coding assistant (Claude Code, Cursor, etc.) as your system/build prompt, or use it directly as your team's engineering brief. It is written so that every hackathon rubric criterion has a corresponding, buildable feature — nothing here is decorative.

> **Refinement changelog (this version):** incorporates the model-selection guidance you provided
> (LightGBM for forecasting, Rule Engine + Isolation Forest for anomaly detection, no third ML
> model for decision support). Eight changes, each traceable to that guidance:
> 1. Module 2's primary forecast is the rate-projection method (always computed, no training
>    needed); LightGBM is folded in as secondary confirmation only — it adds confidence and a
>    feature-importance reason when it agrees and has enough history, and drops out silently
>    otherwise. LightGBM is only as good as the demo dataset it's trained on, so it shouldn't be
>    the sole voice against a live what-if scenario it's never seen — same pattern as Module 3.
> 2. Module 3 names Rule Engine + Isolation Forest, with Isolation Forest kept strictly as a
>    secondary confirmation signal, and skipped below ~10–15 transactions in-window where a fit
>    would be unstable — so every alert still has a plain-language rule behind it.
> 3. New **Module 4 — Decision Intelligence Engine**: Evidence Fusion (configurable weights, not
>    learned), Priority Scoring, Recommendation Engine (ranked actions, not a score), Ownership
>    Engine, Escalation Engine, Explainability — all mapped onto the roles already defined in §3,
>    which themselves trace to the problem statement's own stakeholder list and hierarchy example
>    (brief §5) — no roles invented beyond those two sources.
> 4. Modules 4–8 renumbered to 5–9; every cross-reference (rubric map, demo script, metrics
>    section) updated to match.
> 5. Added a demo-script beat showing the ranked recommendations live.
> 6. Folded "multi-agent decision support" in as a stretch goal — a presentation reskin of
>    Module 4, not a required rebuild.
> 7. Added one optional 8th metric (priority-tier classification accuracy) and one Responsible-AI
>    checklist line ("ranks actions, never a bare score, never a fraud verdict").
> 8. Filled in §7 (previously a placeholder) with just the two new Python dependencies
>    (`lightgbm`, `scikit-learn`) — it still defers to your existing stack for everything else.
>
> Everything else — constraints, RBAC, data simulation, metrics table, deliverables — is
> unchanged.

---

## 1. Role & Mission

You are a senior full-stack engineer and applied-AI product architect. Build a working prototype of a Super Agent Liquidity & Risk Intelligence Platform — a decision-support system for mobile-money "Super Agents" who hold one shared physical cash drawer plus separate, non-fungible e-money balances with multiple providers (bKash, Nagad, Rocket).

The system does not move money, execute transactions, or replace human judgment. It predicts, explains, alerts, and coordinates — humans decide. A dedicated Decision Intelligence layer (§4, Module 4) — not a third ML model — fuses the prediction and anomaly outputs into one prioritized, explainable recommendation; that fusion layer is where the coordination story actually lives.

Core insight to design around: total float can look healthy while one provider's wallet is silently draining. A shop with ৳225,000 in aggregate value can still fail every bKash cash-out request if the bKash-specific balance is ৳5,000. The entire product exists to surface that gap before it causes a service failure.

---

## 2. Hard Constraints (violate none of these)

| Constraint | Why it matters for scoring |
|---|---|
| 100% simulated data — no live provider APIs, no real transactions | Responsible AI (5%) + safety |
| Never output the word "Fraud" — always: "Unusual activity detected. Human review recommended." | Explicit requirement in problem statement |
| Every AI output ships with confidence score + plain-language reason + evidence | Explainability requirement, Data & Analytics (20%) |
| Never issue a confident recommendation on stale/delayed/missing data — confidence must visibly degrade | "Reliability under bad data" metric |
| Strict provider data separation — no UI or role can see cross-provider balances/permissions it isn't entitled to | Responsible AI (5%), Problem Understanding (15%) |
| Risk/Compliance makes the final fraud/compliance call, independent of Operations | Persona requirement — don't let Ops "close" a case as fraud |
| No real PII; synthetic customer/agent IDs only | Privacy requirement |

---

## 3. Users & Role-Based Permissions

Design distinct views/dashboards for each. Build a permission matrix, not just different CSS themes.

| Role | Sees | Can do | Cannot do |
|---|---|---|---|
| Multi-Provider Agent | Own shop's cash + all own provider balances, own shortage forecasts, own recommended next action | Acknowledge own alerts, request rebalancing/support | See other agents' data, close risk cases |
| Provider Operations / Network Coordination | Status & performance of assigned agents, active alerts, agent contact info | Assign field officers, coordinate approved support actions, track case status | Make final fraud/compliance rulings, see raw provider-internal data outside their remit |
| Risk / Compliance Analyst | Escalated cases with full evidence trail, anomaly explanations, historical patterns | Investigate, request more data, make final compliance decision, close/escalate further | Perform operational actions (dispatch officers) |
| Financial Service Provider | Their own provider's performance & pressure metrics across agents | Monitor, export reports | See other providers' balances or decisions — strict wall |
| Management | Aggregated risk-by-geography, recurring issue trends, readiness scorecards | View, drill down, export | Take case-level action (read-only strategic layer) |
| Customers | — (indirect beneficiary only — not a login role) | Better uptime/availability | — |

Build this as an actual RBAC layer (even a simple role-flag + route-guard implementation), not just separate mockup pages — judges evaluating "Implementation" will look for this.

> **Note:** "Field Officer" and "Area Manager," used in Module 6's state machine below, are
> working-title tiers *within* the Provider Operations / Network Coordination role for this
> prototype — not separate RBAC roles. This matches the brief's own hierarchy example (agent →
> field officer → area/district manager → central provider ops), which it explicitly says is
> illustrative rather than an official org chart.

---

## 4. Functional Modules (build in this order — each is independently demoable)

### Module 1 — Unified Multi-Provider Dashboard

- Physical cash + bKash + Nagad + Rocket balances, live/simulated ticking
- Per-provider health indicator (🟢🟡🟠🔴) driven by burn-rate-to-threshold, not just raw balance
- "Overall liquidity" composite score with a one-line reason for the score
- Time-series sparkline per balance (last N hours)

### Module 2 — AI Liquidity Prediction Engine

Output format: *"bKash will face shortage within 40 minutes."*
Must include: confidence %, explanation, evidence (e.g., burn rate, recent transaction cluster, historical pattern match).

**Model: depletion-rate projection (primary) + LightGBM (secondary confirmation)** — the same "never let one model be the sole voice" pattern as Module 3's Rule Engine + Isolation Forest, applied here for the same reason: LightGBM is supervised, and the only realistic training data at hackathon scale is the demo dataset itself, so a model trained on it can't be trusted alone against a live what-if scenario it's never seen.

- **Rate projection** (primary, always computed): rolling outflow velocity per provider → linear projection to zero, confidence derived from the variance of the recent rate (tighter variance → higher confidence). This is the number shown on screen by default — it needs no training, so it's correct-by-construction for whatever data is actually present, including scenarios nobody pre-generated.
- **LightGBM** (secondary confirmation, only when it has enough history): trained on the simulated transaction/balance history with a small, interpretable feature set —

  - rolling outflow velocity (last N intervals)
  - variance of recent outflow rate
  - time-of-day / festival flag
  - current balance as % of typical opening balance
  - provider identity (categorical)

  When LightGBM's prediction and the rate projection agree (within some tolerance), nudge confidence up and add a feature-importance reason (e.g. *"outflow velocity 2.9× baseline (top contributor), festival-day flag active"*) via built-in feature importances or a lightweight SHAP pass. When they disagree, or LightGBM has fewer than ~20 historical snapshots for that provider (cold start, or right after a Module 8 data-quality event), drop it from the output entirely and show the rate projection alone with no confidence boost. LightGBM can only add confidence and a reason — it never overrides or replaces the primary number.

**Explainability:** same rule as Module 3 — every `reasons[]` entry traces back to something computed from the data actually present (rate/variance, or a fired feature importance), never a bare model score.

### Module 3 — AI Anomaly Detection Engine

Detect, at minimum:
- Repeated same-amount transactions
- Abnormal transaction velocity
- Transaction splitting/structuring (clustering of near-threshold amounts)
- Abnormal balance changes
- Suspicious timing
- Location anomalies

Output format (match the problem statement exactly):

```
Repeated 9 transactions
Amount: 4950 BDT
Within: 7 minutes
Confidence: 83%
Reason: Repeated amount pattern
Recommended: Human Review
```

**Model: Multi-headed Rule Engine + Isolation Forest**, combined — not either/or:

- **Rule heads** (primary, each independently explainable): z-score on amount/velocity, sliding-window frequency counts, near-threshold clustering for structuring, repeated-amount bucket counts. Each rule head that fires contributes its own named piece of evidence — this is what makes the exact output format above possible; *"Reason: Repeated amount pattern"* has to trace back to a specific rule, not a fused black-box score.
- **Isolation Forest** (secondary confirmation only — never the sole output): trained on a small multivariate feature set per transaction/window (amount, inter-arrival time, counterparty frequency) to catch combinations of mild irregularities that no single rule threshold would catch alone. Its score is folded into the composite confidence alongside the rule heads; it never fires an alert with no rule-head evidence attached, so every alert keeps at least one plain-language, rule-based reason.

  **Minimum sample size:** below ~10–15 transactions in the current window, an Isolation Forest fit is unstable or meaningless — skip it below that threshold and let the rule heads carry the alert alone (they already have their own minimums, e.g. `min_count=5`, so they don't need a window-size floor the same way). This matters more here than in Module 2, because a thin transaction window is the normal case, not the edge case — a quiet agent, or the first few minutes of any scenario, will routinely fall under it.

This keeps "rule-based + explainable beats a black box" intact — Isolation Forest adds recall without becoming the thing judges can't get an explanation out of, because it's never the only voice in the room.

### Module 4 — Decision Intelligence Engine

**Do not add a third ML model here.** Modules 2 and 3 are already strong, purpose-built models. Layering a generic model on top to "decide what to do" is a common hackathon mistake — it adds a black box exactly where judges most want to see reasoning. Instead, this is a **fusion/orchestration engine**: it combines Module 2 + Module 3 outputs with operational context using transparent, configurable rules — not learned weights.

**Inputs** (per alert-worthy event):
- Forecast output: `hoursToShortage`, confidence, feature-importance reasons (Module 2)
- Anomaly output: rule-head findings + Isolation Forest score + confidence (Module 3)
- System context: provider, area, festival/peak-hour flag, current time, estimated customers waiting
- Operational context: field officer availability, count of this agent's previous alerts, count of currently-open cases

**1. Evidence Fusion Engine** — merges the above into one operational picture instead of five separate numbers, via a **configurable weighted sum**, not a trained model:

```json
// config/decision-weights.json — adjustable per provider/scenario without touching code
{
  "default": {
    "liquidityForecastWeight": 0.35,
    "anomalyWeight": 0.25,
    "customerImpactWeight": 0.20,
    "confidenceWeight": 0.10,
    "dataQualityWeight": 0.10
  },
  "overrides": {
    "nagad": { "liquidityForecastWeight": 0.45, "anomalyWeight": 0.20 }
  }
}
```

Storing weights in config rather than hardcoding them is worth stating explicitly to judges: it demonstrates the system is policy-driven, and that providers could tune priorities without touching the forecasting or anomaly models.

**2. Priority Scoring Engine** — maps the fused score to the same four tiers Module 5 already renders: 0–30 → 🟢 Normal, 31–60 → 🟡 Low Liquidity, 61–80 → 🟠 High Risk, 81–100 → 🔴 Critical. This *is* what decides Module 5's tier — Module 5 no longer computes severity on its own, it just renders whatever this engine outputs.

**3. Recommendation Engine** — the actual innovation surface. Never output a bare priority number; output a **ranked list of safe next actions**, each with its own confidence:

```
Recommended Actions
1. Notify Operations       — 97%
2. Assign Field Officer    — 94%
3. Request Cash Support    — 90%
4. Monitor                 — 72%
5. Risk Review             — 68%
```

The action vocabulary stays limited to the same coordination verbs used everywhere else in this brief — notify, assign, monitor, escalate/review — never "block," "freeze," "reverse," or anything that touches money. You're ranking actions, not predicting fraud — that's the whole point.

**4. Ownership Engine** — auto-assigns the initial case owner using the roles that already exist in §3 (no new roles introduced):

| Trigger pattern | Initial owner | Escalates to |
|---|---|---|
| Liquidity pressure (any severity) | Provider Operations / Network Coordination | stays within Provider Operations (area-manager tier) — it's an operational cash/rebalancing issue |
| Anomaly / unusual pattern | Provider Operations / Network Coordination (initial triage) | Risk / Compliance Analyst (final call — matches §3: "make final compliance decision") |
| Data-quality / feed inconsistency (Module 8) | Financial Service Provider (it's their feed) | Provider Operations notified, so they know not to trust the number until resolved |
| Any Critical-tier alert, regardless of type | (per above) | Management notified for visibility only — §3 already marks Management read-only, so this is a notification, not a reassignment |

**5. Escalation Engine** — time-boxed, notification-only (never auto-resolves or auto-acts):
- Critical: escalate if unacknowledged after 10 minutes
- High: escalate if unacknowledged after 30 minutes
- Low / Informational: no auto-escalation — sits until manually acknowledged

**6. Explainability Engine** — formalizes Module 7: instead of any single module's reasons, writes the fused explanation from every contributing source in one paragraph, e.g.:

> Critical liquidity pressure detected. Predicted provider balance depletion in 17 minutes.
> Transaction frequency is 2.9× higher than the recent baseline. Repeated transaction amounts
> contributed to an elevated anomaly score. Approximately 37 customers could be affected.
> Confidence: 91%. Recommended next step: Assign a field officer and begin operational
> coordination. Human review is required before any further action.

**7. Audit trail** — every fused priority, ranked recommendation, ownership assignment, and escalation this engine produces is written alongside the case's existing state-transition log (Module 6 already tracks owner, timestamps, notes, resolution/escalation reason — this engine just feeds that log rather than needing a separate one).

> **Presentation framing (optional, cheap storytelling upgrade):** you don't need five separate
> autonomous agents to tell this story. On the Alert Detail screen, label the fusion engine's
> inputs as if they were specialist agents reporting in — "Forecast Agent: shortage in 17 min
> (92%)," "Anomaly Agent: high (84%)," "Context Agent: festival, peak hour, 37 customers,"
> "Decision Agent: Critical, assign field officer" — which reads as a multi-agent system to a
> judge without the engineering cost of building one. See §12 if you want the literal version.

### Module 5 — Tiered Alert System

🟢 Normal · 🟡 Low Liquidity · 🟠 High Risk · 🔴 Critical

Alerts are objects with: severity, provider, timestamp, evidence, confidence, status. **Severity is set by Module 4's Priority Scoring Engine, not computed independently here** — this module is purely the rendering/feed layer, which is exactly why the same alert reads consistently everywhere it appears (dashboard badge, ops queue, alert detail).

Real-time feed (WebSocket or short-poll is fine for a prototype).

### Module 6 — Case Management & Escalation Workflow

Implement this exact state machine as a real, enforced flow (not just a static diagram):

```
Alert Generated
      ↓
Assigned to Field Officer
      ↓
Acknowledged
      ↓
Area Manager Review
      ↓
   Resolved  ──OR──  Escalated to Risk Team → Compliance Decision → Closed
```

Every case carries: owner, timestamps per state transition, notes thread, resolution/escalation reason. This satisfies both "Operational Coordination" and gives Risk/Compliance their independent final-decision step.

**Initial ownership and escalation timing now come from Module 4's Ownership Engine and Escalation Engine** rather than being hardcoded per alert — "Assigned to Field Officer" happens automatically per the Ownership Engine's mapping table, and the jump to "Area Manager Review" (or straight to Risk Team for anomaly-type cases) fires automatically on the Escalation Engine's timers if nobody has acknowledged yet. A human can always act sooner; the timer only fires if nobody does.

### Module 7 — Explainability Layer

Never show a bare score. Always render:

```
Why?
• Cash decreased 40%
• Provider balance nearly empty
• Transaction velocity increased
• Similar pattern observed previously
Confidence: 82%
```

Build this as a reusable component fed by a structured `reasons[]` + `confidence` object from every AI module — consistency here signals engineering maturity to judges. **As of Module 4, this component's input is the Decision Intelligence Engine's fused explanation** (which itself draws on Modules 2 and 3's own `reasons[]`), not any single module's output in isolation — so a judge clicking into any alert sees one coherent paragraph, not three separate score cards stitched together.

### Module 8 — Data Quality & Confidence Monitor

Simulate provider API delay/outage/inconsistency scenarios.

When triggered, every affected prediction must visibly downgrade:

```
Confidence reduced
Reason: Rocket API delayed
Recommendation: Wait for provider update
```

This module is what proves "Responsible AI" is real, not a slide. **It also feeds Module 4's Evidence Fusion Engine directly** — a degraded `dataQualityWeight` input should pull the fused confidence down too, not just the individual forecast/anomaly confidences, so a bad-data event can't get masked by an otherwise-strong fused score.

### Module 9 — Metrics & Model Validation Dashboard

A dedicated screen showing the required quantitative metrics computed against your simulated ground truth (see §6). This directly targets the highest-leverage, most-often-skipped rubric line. **If time allows, also show the optional 8th metric (§6) validating Module 4's priority classification against your injected scenario labels** — it's the cheapest way to prove the fusion engine isn't just theatre.

---

## 5. Data Simulation Engine

Build a synthetic data generator, not a static seed file — judges will ask "what happens if I change the scenario live."

Seed baseline from the problem statement's own example:

| Resource | Balance |
|---|---|
| Physical Cash | ৳100,000 |
| bKash Wallet | ৳5,000 |
| Nagad Wallet | ৳80,000 |
| Rocket Wallet | ৳40,000 |

- Configurable "normal" transaction stream per provider (Poisson-ish arrival, realistic amount distribution)
- Injectable scenarios (toggle in a debug panel for the live demo):
  - Sudden bKash cash-out surge → triggers Module 2 prediction
  - Repeated-amount burst → triggers Module 3 anomaly
  - Structuring pattern (many transactions just under a threshold)
  - Rocket API delay/outage → triggers Module 8
- Log ground-truth labels (which injected events were "true anomalies") so you can compute precision/recall honestly in Module 9. If you're building the optional priority-classification metric (§6), also log the *intended* severity tier for each injected scenario (e.g., Scenario A hidden shortage = High/Critical, salary-day control = Normal) alongside the anomaly ground truth.

---

## 6. Required Quantitative Metrics (implement and display all — minimum 3, aim for all 7, 8 if you build the optional Module 4 validation)

| Metric | How to compute in a simulated environment |
|---|---|
| Liquidity prediction accuracy/error | Compare predicted shortage time vs. simulated actual depletion time (MAE in minutes) |
| Shortage detection lead time | Time between alert and simulated actual shortage event |
| Anomaly precision & recall | Against your injected ground-truth anomaly labels |
| False-positive rate | Normal simulated events incorrectly flagged |
| Alert explanation coverage | % of alerts rendered with full `reasons[]` + confidence (should be 100% — prove it) |
| API/processing latency | Instrument and display p50/p95 response times |
| Reliability under bad data | Confidence delta when Module 8 delay/outage is injected vs. normal conditions |
| Priority classification alignment *(optional, §4 Module 4)* | % of injected scenarios where the fused priority tier (Normal/Low/High/Critical) matches the scenario's intended severity label — validates the Decision Intelligence Engine itself, not just the two ML modules feeding it |

---

## 7. Suggested Technical Architecture

apps/web (Next.js 15 App Router + TypeScript)
Pure UI + React Query (or SWR).
DataClient interface with mockClient (local JSON / in-memory) for Phase 1–2.
Switch to apiClient (direct fetch to http://localhost:8000) in Phase 3.

apps/api (Python + FastAPI + SQLModel + SQLite)
Everything: models, CRUD, workflow (alerts/cases/state machine), analytics, explanations.
Use SQLModel for typed models that double as API responses.
Add CORSMiddleware for the Next.js origin.

Core connective tissue first (as emphasized)
Alert as the central entity that links liquidity forecast + anomaly + owner + status + audit trail.
State machine for alert lifecycle (open → assigned → reviewing → resolved).
Advisory language everywhere + confidence indicators.

Phase plan aligned to rubric
Phase 1: Mock data + connected dashboard (liquidity + anomaly feed into same alerts). Hits Problem understanding, Innovation, UX.
Phase 2: Synthetic data testing, confidence bands, evidence, false-positive notes. Hits Data quality.
Phase 3: Real FastAPI backend swap (adapter makes this low risk). Hits Technical implementation.
Phase 4: Polish (Bengali/Banglish, explainability tooltips, "what this won't do" note, human gates), video recording.


This keeps every hour traceable to rubric line items and minimizes "works on my machine" surprises.
The collapsed architecture is the higher-probability path to top rank given the constraints. Build the connective tissue (shared alert/case model) aggressively first.. The one addition this refinement requires: the Python analytics service needs `lightgbm` and `scikit-learn` (for `IsolationForest`) added to its dependencies, alongside whatever it already has for the rule engine and API layer.

---

## 8. Responsible AI Checklist (score this explicitly before demo day)

- [ ] Word "fraud" does not appear anywhere in system output
- [ ] Every risk/anomaly output uses "Unusual activity detected. Human review recommended."
- [ ] Every AI output has confidence + explanation + evidence
- [ ] Confidence visibly degrades under delayed/missing data
- [ ] No cross-provider data leakage in any role's view
- [ ] Risk/Compliance role has an independent, final decision step separate from Operations
- [ ] No real transactions are ever executed; this is stated on-screen somewhere (e.g., a "Simulated Environment" badge)
- [ ] Decision Intelligence Engine (Module 4) outputs a ranked list of safe actions with confidence — never a bare risk score and never a fraud verdict

---

## 9. Demo Script (aim for 5–6 minutes, map every beat to a rubric line)

1. Open on the shop analogy (30s) — restate the cash-drawer-vs-three-wallets problem in one breath. *(Problem Understanding)*
2. Show the live dashboard, point at bKash going yellow. *(UX)*
3. Trigger the surge scenario → liquidity prediction fires with confidence + explanation. *(Innovation, Data & Analytics)*
4. Trigger the repeated-amount scenario → anomaly alert appears using the exact "Unusual activity / Human review" language. *(Responsible AI)*
5. Open that alert's detail view and point at the Decision Intelligence Engine's output: fused priority (Critical), the ranked recommended actions with their own confidences, and the auto-assigned owner. Say the line out loud: *"It ranks actions, it doesn't predict fraud."* *(Innovation — the highest-leverage 15 seconds of the whole demo)*
6. Walk one alert through the full case-management state machine to Resolved, then walk a second one to Escalated → Risk Team makes an independent call. *(Implementation, Operational Coordination)*
7. Trigger the Rocket API delay → show confidence downgrading live, and show it pulling the Module 4 fused confidence down too, not just the raw forecast. *(Responsible AI, Reliability)*
8. Close on the Metrics dashboard — precision/recall, lead time, latency, all computed on-screen. *(Data & Analytics — this is the highest-weighted category most teams skip; ending here is deliberate.)*

---

## 10. Judging Rubric → Feature Map

| Criterion | Weight | Where you demonstrate it |
|---|---|---|
| Problem Understanding | 15% | §1 opening framing, RBAC design (§3) |
| Innovation | 20% | Modules 2, 3, 4, 7 (prediction, anomaly, decision fusion — the connective tissue judges are told to look for — and explainability beyond a static dashboard) |
| Implementation | 25% | Full-stack build across §4–§7, real state machine, real RBAC, config-driven decision fusion (not hardcoded) |
| Data & Analytics | 20% | Module 9 + §6 metrics table |
| User Experience | 10% | Role-specific dashboards (§3) |
| Responsible AI | 5% | §8 checklist, Module 8 |
| Demo | 5% | §9 script |

---

## 11. Deliverables Checklist

- [ ] Working repo with README (setup instructions, architecture diagram)
- [ ] Live or recorded demo following §9
- [ ] Metrics dashboard with real (simulated) numbers, not placeholders
- [ ] One-page architecture diagram (frontend / backend / AI layer / decision fusion layer / data simulation engine)
- [ ] `decision-weights` config file (or equivalent), documented in the README, showing the fusion engine's weights are adjustable without code changes
- [ ] Explicit "Responsible AI" section in your README mapping to §8

---

## 12. Stretch Goals (only if core is done early)

- "What-if" simulation controls exposed to judges live (let them inject a scenario themselves)
- Geographic heat-map for Management view
- Simple notification stub (SMS/email simulated, not sent) for field officer assignment
- Historical pattern-matching ("similar anomaly seen on [date]") using your own logged simulation history
- Literal multi-agent decomposition of the Decision Intelligence Engine (separate Forecast/Anomaly/Context/Coordination "agents" feeding a Decision Agent) — mostly a presentation/orchestration reframing of Module 4, not new capability. The cheap version (labeling Module 4's existing inputs as if they were agents) is already folded into Module 4 above; only build the literal version if §4 Modules 1–9 are solid with hours to spare.
