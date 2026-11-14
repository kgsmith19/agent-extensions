# Owner Stand-In AC2a broker protocol core

Parent: `kgsmith19/agent-extensions#98`
Child: `kgsmith19/agent-extensions#111`

## Outcome

Add a provider-neutral, non-deployed broker protocol core that issues one-use
WebAuthn challenges, consumes verified assertions at a typed boundary, and
mints revocable, short-lived, session-bound capability grants. Persisted state
and receipts contain only identifiers, timestamps, scopes, versions, and
digests.

## Delivery sequence

1. Add failing tests for bindings, replay, expiry, revocation, redaction,
   collision handling, and reconciliation.
2. Implement the in-memory protocol core with injected clock, entropy, and
   assertion verifier.
3. Add concise security-boundary documentation and route the README.
4. Run focused tests, mutation checks, the full test suite, shell/PowerShell
   gates, and Ruff.
5. Publish one child PR with exact-head CI evidence. Do not claim live broker
   authority or check parent AC2 until a real provider adapter and deployment
   are proven.

## Rollback

Revert the child PR. This slice has no provider-side identity, credential,
passkey, deployment, session, or permission to revoke.
