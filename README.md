# Super Agent Liquidity & Risk Intelligence Platform

A safe, synthetic decision-support prototype for multi-provider agents and provider teams. It combines shared physical-cash visibility with strictly separate provider e-money positions, forecasts liquidity pressure, explains unusual behavior with uncertainty, and tracks human-owned cases. It never executes a financial transaction or makes a final fraud determination.

## What the prototype demonstrates

- A unified outlet view of physical cash and separate bKash, Nagad, and Rocket balances.
- Provider-level forecasts plus aggregate pressure based on the earliest independent physical-cash or provider-wallet constraint, with ETA, confidence, evidence, and degraded-data fallback.
- Explainable repeated-amount, velocity, structuring, and balance/data-quality signals using careful “requires review” language.
- Role-scoped agent, operations, risk, provider, and management views with provider data walls.
- Alert ownership, acknowledgement, assignment, escalation, risk-to-Operations return, notes, risk recommendation, support request, resolution, closure, and audit history.
- English, বাংলা, and Banglish explanations. Vendor-generated explanations are optional; a deterministic safe fallback always remains available.
- Scenario injection and measurable forecast, anomaly, explanation, latency, data-quality, and priority metrics.

## Run locally

Prerequisites: Python 3.11+ and Node.js 20+.

```bash
cd apps/api
python -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
cp .env.example .env
set -a; source .env; set +a
uvicorn app.main:app --reload --port 8000
```

In another terminal:

```bash
cd apps/web
npm ci
npm run dev
```

Open <http://localhost:3000>. The API documentation is at <http://localhost:8000/docs>. No explanation API key is required; without one, explanations use the deterministic fallback.

The database is created and seeded at `.data/super_agent.db`. Set `SUPER_AGENT_DATA=/another/directory` before starting the API to use an isolated database. Delete the local database only when you intentionally want to reset the synthetic demo.

## Demo flow

1. Start as **Demo Agent** and inspect physical cash, separate provider balances, forecast evidence, and confidence.
2. In **Synthetic scenario lab**, run **bKash e-money drawdown**. The control injects data and runs the analytical cycle automatically; open the resulting liquidity alert.
3. Run **Repeated Nagad amounts** or **Amounts near ৳5,000**, then inspect the record-level evidence and uncertainty.
4. Switch to **Operations**, assign/acknowledge the case, add a note, and follow the escalation path.
5. Switch to the matching **Provider** to verify that only its provider column and authorized actions are visible.
6. Run **Delayed Rocket feed** to see reduced confidence and fallback behavior, then resolve it as Operations or Rocket.
7. Run **Nagad salary-day volume** as a legitimate-demand control and verify that compatible volume alone is not promoted as unusual activity.
7. Open **Metrics** to inspect measured quality and simulation ground truth.

## Verify

```bash
cd apps/api && python -m pytest -q
cd ../web && npm run build
```

Metrics shown in the UI are calculated from the current synthetic run. Unevaluated cold-start metrics are shown as `n/a`, never as perfect performance.

## Design documentation

- [Architecture and provider boundaries](docs/architecture.md)
- [Synthetic data and validation](docs/data-and-validation.md)
- [Responsible design and limitations](docs/responsible-design.md)

## Configuration and secrets

`apps/api/.env.example` contains names and safe defaults only. Never commit a populated `.env` or API key. If a real credential was ever copied into a tracked file, revoke it at the provider because removing it from the latest revision does not erase repository history.
