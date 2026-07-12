# Architecture and provider boundaries

```text
Next.js role-aware UI (port 3000)
             |
             | REST + X-User demo identity
             v
FastAPI routers (port 8000)
  | dashboard/read models       | alert and case workflow
  | simulation/scenarios        | notifications/support requests
  | metrics/config              | role + provider authorization
             |
             v
Deterministic analytics and orchestration
  | liquidity forecast          | anomaly rules / optional Isolation Forest
  | confidence + data quality   | evidence fusion / ranked safe actions
  | deterministic explanation fallback
  | optional LLM wording only (never a business decision)
             |
             v
SQLite / SQLModel synthetic data
  agents | provider balances | transactions | forecasts | alerts | cases
  scenario ground truth | audit events | notifications | metric ticks
```

## Data flow

The simulation engine writes synthetic provider-tagged transactions and balances. Liquidity and anomaly services derive forecasts and evidence. The orchestrator fuses related signals into advisory alerts and assigns a responsible role. Case services apply the human-review state machine and retain notes and audit events. Read-side services shape results for the authenticated demo principal before the UI receives them.

## Provider separation

Provider balances are stored as distinct rows tagged by provider; there is no conversion or settlement operation. The outlet aggregate-pressure view selects the earliest independent constraint across physical cash and provider wallets. It never adds balances or derives service time from a pooled value. Provider principals are filtered in backend queries and response shaping to their own provider; other providers' balances, derived forecasts, evidence, and cross-position pressure are not returned. Provider actions are checked again on the API, not merely hidden in the UI.

Physical cash is a shared operational constraint, not an interoperable wallet. Recommendations are support or coordination requests through approved human channels. They do not transfer cash or e-money.

## Coordination flow

```text
signal -> advisory alert -> owner/queue -> assignment -> acknowledgement
       -> investigation/review -> optional provider or risk escalation
       -> risk advisory -> Operations follow-up -> human resolution reason
       -> closed case + immutable-style audit trail
```

The prototype also creates role-scoped notifications and supports provider-specific cash-support requests. A support request records intent and workflow state; it is not a real financial instruction.

## Technology

- Next.js 15, React 19, TypeScript, and SWR
- FastAPI, SQLModel, SQLite, scikit-learn, and LightGBM
- Optional Groq, Grok, or Gemini explanation wording with validation and deterministic fallback
