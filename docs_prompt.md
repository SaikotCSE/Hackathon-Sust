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
- `docs/data-and-validation.md`