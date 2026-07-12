# Responsible design and limitations

## Human-review boundary

Every risk signal is advisory. The system uses language such as “unusual,” “possible,” and “requires review”; it does not label fraud, block accounts, discipline an agent, or reach a final compliance decision. A human owns each important case, sees the evidence and uncertainty, records notes, and supplies a resolution reason. Escalated cases can receive an advisory Risk recommendation and return to Operations for follow-up, resolution, and closure; Risk never makes or records a final wrongdoing determination.

The optional language model can only rewrite supplied evidence into a constrained explanation. Deterministic code calculates forecasts, priority, ownership, and recommended action keys. Model output is validated for advisory language and allowed actions; failure, timeout, missing credentials, or invalid output activates a deterministic explanation.

## Actions intentionally not implemented

- real wallet access, transfer, refill, recovery, reversal, conversion, or settlement;
- automatic blocking or final fraud determination;
- collection of customer identity, PIN, OTP, password, private key, or production credential;
- control by one provider over another provider's balance, data, or decision; and
- claims of regulatory approval or production readiness.

## Privacy and security

Only synthetic identifiers and contacts are seeded. Provider scoping is enforced at API boundaries and tested. Explanation-call audit rows intentionally omit secrets. Local `.env` and SQLite data are ignored by Git.

The role switcher uses an `X-User` header and seeded demo identities. This is suitable only for a hackathon demonstration, not real authentication. Production work requires an identity provider, signed sessions/tokens, least-privilege claims, CSRF and rate-limit controls, hardened CORS, encrypted transport/storage, secret management, retention rules, and an authorization review.

## False positives and fairness

Repeated amounts, velocity, or threshold clusters can result from legitimate events such as salary day, festivals, connectivity recovery, or local demand. The prototype includes a legitimate-volume negative control and reports false-positive rate, but the synthetic sample cannot establish fairness across real populations. No protected or demographic attributes are used. Before deployment, providers would need representative validation, subgroup error analysis where legally and ethically appropriate, documented thresholds, appeal/review processes, and ongoing drift monitoring.

## Operational limitations

- SQLite and in-process simulation are demonstration components, not a resilient multi-user deployment.
- Additive startup migrations are intentionally lightweight; production requires versioned migrations and backups.
- Datetimes are currently stored as naive UTC values. Python 3.13 reports deprecation warnings for `datetime.utcnow()`; behavior is consistent in the prototype, but a production migration should use timezone-aware UTC end to end.
- Metrics are meaningful only after the matching scenarios have generated both predictions and outcomes.
- Synthetic provider names illustrate separation and do not imply production API access or endorsement.
