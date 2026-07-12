# Prompt log

This file records the user prompts that drove each commit on top of
`012c6ca` (`chore: remove local-only hackathon test harness and ignore
it`). The prompts are reconstructed from this development session —
they are the user requests as understood at the time, not the literal
verbatim transcript.

## Commit 1 — `4ab4d87` — fix(api): align balance deltas with provider e-money vs physical cash flow

> The simulation engine and the balance-drop anomaly detector were
> applying the wrong sign for the agent-side effect of a transaction:
> cash-out was recorded as e-money leaving the agent (when in reality
> e-money arrives while cash leaves the drawer), and cash-in was
> recorded as e-money arriving (when it is consumed). Reverse the sign
> in both places and add regression tests so the direction of the rule
> matches the agent's daily mental model and the bKash-surge scenario
> actually draws down the bKash position.

Files touched:

- `apps/api/app/services/anomaly.py`
- `apps/api/app/simulation/engine.py`
- `apps/api/tests/test_anomaly_detection.py`

## Commit 2 — `43f699d` — fix(api/alerts): scope liquidity-support requests per outlet+provider and reuse active ticket

> Cash-support requests were being keyed off whichever alert was at the
> top of the dashboard, so a bKash request for one outlet could be
> replaced by a Nagad request the moment a Nagad alert rose to the
> top, and two related alerts for the same outlet could spawn
> duplicate tickets. Scope uniqueness to (agent, provider), reuse an
> active request when one already exists, and surface reused/created
> status plus provider and origin alert on the response so the UI can
> present a single coherent ticket.

Files touched:

- `apps/api/app/routers/alerts.py`
- `apps/api/tests/test_cash_support.py`

## Commit 3 — `ee12c41` — fix(api/snapshots): only surface open alerts in agent snapshots

> Agent snapshots were listing every alert regardless of state, which
> let resolved or closed tickets remain visible on the outlet card and
> in the dashboard queue long after human review completed. Filter to
> alerts whose status is not resolved or closed in both single-agent
> and batch snapshot paths so the view matches what the case lifecycle
> has actually accepted as still open.

Files touched:

- `apps/api/app/services/snapshots.py`

## Commit 4 — `ee4e650` — feat(api/scenarios): validate inject inputs and return execution metadata

> The scenario inject endpoint accepted almost anything: an unknown
> provider, an out-of-range duration, a non-numeric label, or a kind
> that is bound to a specific provider but pointed at another. Those
> inputs silently landed in the simulation engine and produced
> transactions the downstream detector and the UI could not trust.
> Tighten the endpoint to reject these payloads with explicit 400s,
> return the scenario event id, kind, provider, severity, duration,
> and an `analysis_required` flag so callers know a tick is still
> needed before the scenario has visible evidence, and add end-to-end
> tests that exercise each scenario kind through the public router.

Files touched:

- `apps/api/app/routers/scenarios.py`
- `apps/api/tests/test_scenario_controls.py` (new)
- `apps/api/tests/test_scenario_end_to_end.py` (new)

## Commit 5 — `957cc51` — chore: rename 'request cash support' to 'request provider liquidity support'

> The action the agent takes is a request to one financial service
> provider for its own e-money position, not a shared cash request.
> The shorter label mis-described the workflow and contradicted the
> new provider-scoped cash-support router logic. Rename the label in
> the decision-weight catalog (and its fallback) to 'Request Provider
> Liquidity Support' so the UI, configuration, and alert execution
> path agree on what the workflow actually does.

Files touched:

- `apps/api/app/services/decision_weights.py`
- `config/decision-weights.json`

## Commit 6 — `c7b1809` — feat(web): refactor decision panel into a provider-scoped priority queue

> The dashboard's recommendation panel was built around a single fused
> top alert, which silently hid every other open case and lost the
> provider-scoped state that the cash-support fix now produces. Replace
> it with a priority queue: every open alert (and pressured forecast)
> is listed, the highest-priority item is selected first, and acting
> on it advances to the next. Each recommendation card shows the
> responsible owner, the provider target, an active-support indicator
> that disables duplicate 'Request Liquidity' clicks, and the
> per-action owner hint. Add a `RecommendedActionResult` type so the
> client surfaces reused/created status and the resolved support
> ticket metadata.

Files touched:

- `apps/web/components/DecisionRecommendationPanel.tsx`
- `apps/web/lib/client.ts`
- `apps/web/lib/types.ts`

## Commit 7 — `afc3895` — feat(web): redesign scenario lab with one-click run and visible feedback

> The dashboard scenario controls were a row of identically coloured
> buttons whose labels only described a kind, not what they were
> meant to demonstrate. Each click also only recorded the scenario
> and left the user to find and press 'Tick simulation' to see
> anything happen, which made the demo flow brittle for reviewers.
> Replace the row with a 'Synthetic scenario lab' grid: each card has
> a category, provider tag, description, expected behaviour, and an
> accent colour. The inject call now also runs one analytical tick,
> refreshes SWR, and shows a status banner with the number of
> transactions analysed, alerts created, and feed-quality impact, so
> every control has an immediate, observable result without any extra
> step.

Files touched:

- `apps/web/app/dashboard/page.tsx`

## Commit 8 — `5505353` — docs: refresh demo flow and scenario descriptions

> The README walkthrough and the data-and-validation scenario table
> still described the controls as 'bKash surge', 'Repeated amounts',
> 'Structuring', and 'Salary day', and assumed that injecting and then
> separately ticking the simulation were required steps. The
> dashboard scenario lab now does both in one click and uses new names
> that make the provider and the expected behaviour explicit. Update
> the demo flow and the scenario table to match the new labels, the
> new behaviour of the Rocket feed delay scenario (low intended
> severity and a visible fallback), and the new Nagad salary-day
> negative control.

Files touched:

- `README.md`
- `docs/data-and-validation.md`| 13  | `a683d7f` | SaikotCSE  | feat(web): charted dashboard with per-provider burn rate |
| 14  | `5a7454d` | SaikotCSE  | fix(web): full-width layout and standard header scale |
| 14  | `5a7454d` | SaikotCSE  | fix(web): full-width layout and standard header scale |
| 15  | `aa3f254` | SaikotCSE  | fix(api): outlier-resistant burn rate in liquidity projection |
| 16  | `910c3df` | SaikotCSE  | fix(api): natural-language shortage ETA in alert titles |
| 17  | `cdf09c8` | SaikotCSE  | feat(web,api): combined liquidity picture on agent dashboard |
| 18  | `f7d041b` | SaikotCSE  | fix(web): use live provider name in Banglish alert headline |
| 19  | `3d95f88` | SaikotCSE  | fix(dashboard): match ops area with prefix so sub-areas surface |
| 20  | `17a6b58` | SaikotCSE  | fix(alerts): return 409 for state-machine violations instead of 500 |
| 21  | `d5e8bac` | SaikotCSE  | refactor(rbac): rename can_close_fraud -> can_close_compliance_case |
| 22  | `9a7e8b6` | SaikotCSE  | fix(api): harden auto-escalation to be routing-only |
| 23  | `8759e8a` | SaikotCSE  | feat(web): frame confidence and priority as evidence, not verdicts |
| 24  | `1cbe598` | dev        | fix(api): hard-scope provider dashboard to principal's own column |
| 25  | `4c0449e` | SaikotCSE  | feat(web): add degraded fields to DashboardProvider type |
| 26  | `9e18b5c` | SaikotCSE  | feat(web): render degraded badge and read server shortage_eta_human |
| 27  | `e17bc8c` | SaikotCSE  | feat(web): defense-in-depth filter providers and alerts in ProviderView |

## Phase 1 — initial scaffolding (Jul 11, 25 commits by `SaikotCSE` + 1 by `dev`)

(The 27 entries immediately above this heading — numbered 1 through 27 —
belong to Phase 1. They were left in place above the divider that
separates the eight Phase-5 commit prompts at the top of this file
from the per-phase history below.)

## Phase 2 — multi-agent + management dashboard (Jul 12, `SaikotCSE`, 10 commits)

| #   | Hash      | Author     | Subject |
|-----|-----------|------------|---------|
| 28  | `72533fd` | SaikotCSE  | fix(snapshots): score empty wallets as critical and surface missing data as unknown |
| 29  | `e20c95b` | SaikotCSE  | feat(seed): multi-agent dataset with per-minute history for burn-rate forecasts |
| 30  | `5ffd055` | SaikotCSE  | feat(api/dashboard): management rollup with filters, pressure buckets, recurring problems |
| 31  | `682911a` | SaikotCSE  | feat(web/dashboard): management view with filter bar, pressure buckets, recurring problems |
| 32  | `1b0a128` | SaikotCSE  | fix(web): correct AlertCard prop name and guard against undefined |
| 33  | `309c3ec` | SaikotCSE  | fix(web/components): type SeverityPill against the real Severity union |
| 34  | `fd36317` | SaikotCSE  | feat(web/rbac): add per-recommended-action RBAC table |
| 35  | `68c81aa` | SaikotCSE  | feat(api/alerts): add /alerts/{id}/action endpoint with role enforcement |
| 36  | `16a3a4f` | SaikotCSE  | feat(web): wire client.executeRecommendedAction + role-filtered decision panel |
| 37  | `a322eed` | SaikotCSE  | fix(api/engine): clamp _draw_tx weights so depleted providers don't crash random.choices |

## Phase 3 — forecast polish, perf, role cleanup (Jul 12, `Claude`, 22 commits)

| #   | Hash      | Author     | Subject |
|-----|-----------|------------|---------|
| 38  | `cbffdf3` | Claude     | feat(api/forecast): add curated summary field to ForecastSnapshot and ProviderSnapshot |
| 39  | `0d95c2b` | Claude     | refactor(api/forecast): raise rate_projection confidence floor + curated summary |
| 40  | `0f0811d` | Claude     | feat(api/forecast): loosen LightGBM cold-start; tune compute_forecast DQ penalty |
| 41  | `fe97cdb` | Claude     | feat(api/forecast): surface curated summary through dashboard + orchestrator |
| 42  | `01984e2` | Claude     | fix(api/db): additive migrations for new columns on existing SQLite files |
| 43  | `7ce60b4` | Claude     | feat(web/forecast): minimal ForecastTimeline with curated summary basis line |
| 44  | `9a2f79f` | Claude     | perf(api): stop running orchestration cycle on every page request |
| 45  | `6dc39ff` | Claude     | perf(api): batch-load dashboard snapshots to eliminate N+1 queries |
| 46  | `a277594` | Claude     | perf(api): cache LightGBM forecast models with 60s TTL |
| 47  | `087a140` | Claude     | perf(api): cache Isolation Forest anomaly scores with 60s TTL |
| 48  | `15d5a15` | Claude     | perf(api): bound unbounded history queries + add composite indexes |
| 49  | `fc0afbd` | Claude     | perf(web): reduce SWR polling intervals from 5s to 15s |
| 50  | `3f26352` | Claude     | refactor(api/seed): drop area-manager demo user |
| 51  | `5be0471` | Claude     | refactor(api): drop area-manager tier from decision engine |
| 52  | `7591e53` | Claude     | refactor(web): drop area-manager tier label from RBAC and panel |
| 53  | `681d129` | Claude     | refactor(api): rename risk-role label to 'Risk analyst' |
| 54  | `56805ce` | Claude     | refactor(web): rename ops + risk role labels in dropdown |
| 55  | `97d0033` | Claude     | fix(web/dashboard): stop serving stale view after role or agent change |
| 56  | `816013d` | Claude     | perf(web/dashboard): keep previous view during role/agent switch + fix SSR hydration |
| 57  | `a39609f` | Claude     | feat(api): complete decision support and case workflows |
| 58  | `27c12f5` | Claude     | feat(web): integrate review and coordination workflows |
| 59  | `c8896b1` | Claude     | chore(git): ignore root runtime data |

## Phase 4 — guardrails, RBAC, escalation, build upgrades (Jul 12, `Claude`, 13 commits)

| #   | Hash      | Author     | Subject |
|-----|-----------|------------|---------|
| 60  | `ff107b8` | Claude     | fix(api): restrict case coordination to provider operations |
| 61  | `2280cbe` | Claude     | fix(web): hide operations coordination from field officers |
| 62  | `4c569d6` | Claude     | fix(api): remove leaked Groq API key from .env.example |
| 63  | `82002d7` | Claude     | fix(api): keep evaluation labels out of detector inputs |
| 64  | `4e7ac25` | Claude     | feat(api,web): show earliest independent position as aggregate pressure |
| 65  | `fbf95a2` | Claude     | fix(api,web): never report perfect metric scores when evaluation is empty |
| 66  | `04cede6` | Claude     | feat(web): apply management dashboard filters server-side |
| 67  | `e379ff5` | Claude     | fix(api/dashboard): tighten provider wall and re-score after filters |
| 68  | `158ca7e` | Claude     | feat(api,web): let Risk return an escalated case to Operations |
| 69  | `b14cdc5` | Claude     | build(web): upgrade to Next 15.5 / React 18.3 and add ESLint |
| 70  | `00a6a17` | Claude     | chore(web): drop dead code and unused imports |
| 71  | `7bdd503` | Claude     | docs: add README and ship the 9-layer + Killer-Judge test harness |
| 72  | `012c6ca` | Claude     | chore: remove local-only hackathon test harness and ignore it |

## Phase 5 — this session, eight logical fixes (Jul 12, `Claude`, 8 commits)

The detailed user prompts for these are at the top of this file.

| #   | Hash      | Author     | Subject |
|-----|-----------|------------|---------|
| 73  | `4ab4d87` | Claude     | fix(api): align balance deltas with provider e-money vs physical cash flow |
| 74  | `43f699d` | Claude     | fix(api/alerts): scope liquidity-support requests per outlet+provider and reuse active ticket |
| 75  | `ee12c41` | Claude     | fix(api/snapshots): only surface open alerts in agent snapshots |
| 76  | `ee4e650` | Claude     | feat(api/scenarios): validate inject inputs and return execution metadata |
| 77  | `957cc51` | Claude     | chore: rename 'request cash support' to 'request provider liquidity support' |
| 78  | `c7b1809` | Claude     | feat(web): refactor decision panel into a provider-scoped priority queue |
| 79  | `afc3895` | Claude     | feat(web): redesign scenario lab with one-click run and visible feedback |
| 80  | `5505353` | Claude     | docs: refresh demo flow and scenario descriptions |
| 81  | `20122e8` | Claude     | docs: add prompt log for the 8 commits since the harness cleanup |

## Conventional-commit type tally

Pulled with `git log --no-merges --format="%s" | awk '{print $1}' | sort | uniq -c | sort -rn`.

| Type         | Count |
|--------------|-------|
| `feat`       | 33    |
| `fix`        | 21    |
| `perf`       | 8     |
| `refactor`   | 7     |
| `chore`      | 6     |
| `docs`       | 3     |
| `build`      | 1     |
| **Total**    | **81** |

Breakdown of the `feat` and `fix` families:

- `feat(web)` — 13
- `feat(api)` — 7
- `feat(api/forecast)` — 3
- `feat(api,web)` — 2
- `feat(api/scenarios)`, `feat(api/dashboard)`, `feat(api/alerts)` — 1 each
- `feat(web/rbac)`, `feat(web/forecast)`, `feat(web/dashboard)` — 1 each
- `feat(web,api)` — 1
- `feat(seed)`, `feat(config)` — 1 each
- `fix(api)` — 8
- `fix(web)` — 4
- `fix(web/dashboard)`, `fix(web/components)`, `fix(snapshots)`, `fix(dashboard)` — 1 each
- `fix(api/snapshots)`, `fix(api/engine)`, `fix(api/db)`, `fix(api/dashboard)`, `fix(api/alerts)` — 1 each
- `fix(api,web)` — 1
- `fix(alerts)` — 1

`perf` and `refactor` family breakdown:

- `perf(api)` — 5, `perf(web)` — 1, `perf(web/dashboard)` — 1, `perf(api/db)` lives under `fix`.
- `refactor(web)` — 2, `refactor(api)` — 2, `refactor(rbac)`, `refactor(api/seed)`, `refactor(api/forecast)` — 1 each.

Authors observed in the log:

- `SaikotCSE` — initial scaffolding + multi-agent/management dashboard
- `dev` — single early fix on provider scoping (`1cbe598`)
- `Claude` — forecast polish, perf, role cleanup, guardrails, build upgrades, and the eight logical fixes in this session
