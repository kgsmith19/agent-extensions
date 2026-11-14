# Owner Stand-In broker protocol core

Issue #111 is the non-deployed AC2a protocol slice of canonical parent #98. It
does not enroll a passkey, expose an endpoint, create a provider account, hold a
long-lived credential, or grant live authority.

## Enforced state machine

1. The broker core generates at least 256 bits of challenge entropy.
2. Persisted state retains the challenge digest—not the challenge—and binds it
   to one RP ID, exact HTTPS origin, logical identity, client-session digest,
   and expiry.
3. The first authentication attempt consumes the challenge before any adapter
   call. Binding failure, adapter rejection, timeout, or malformed results all
   require a new challenge.
4. Cryptographic WebAuthn verification runs outside the global state lock so an
   unavailable authenticator cannot block an unrelated revocation.
5. The core rechecks expiry and every verified binding after the adapter
   returns, then mints an opaque, scope-bounded grant whose token is stored only
   as a digest. Session and grant share the same maximum 15-minute expiry.
6. Authorization rechecks the bearer digest, client-session digest, session,
   scope, expiry, and revocation state. Fixed error codes and evidence receipts
   contain no verifier payload or bearer material.

This follows the WebAuthn requirement for server-generated randomized
challenges, exact expected-challenge comparison, expected-origin validation,
and RP ID hash verification described in the W3C Web Authentication Level 3
specification: <https://www.w3.org/TR/webauthn-3/>.

## Provider adapter contract

The injected verifier is trusted only to perform cryptography. It must validate
the WebAuthn `webauthn.get` type, challenge, exact expected origin, RP ID hash,
signature, credential, user-presence and required user-verification flags. It
returns identifiers and digests only. The core independently compares every
returned binding and rejects malformed results.

Adapter inputs are transient. Implementations must not log assertion payloads,
client data, authenticator data, signatures, credential IDs, challenges, or
validation exceptions. No adapter may translate a failed verification into a
successful result.

## Detection and repair

- Detector: `owner_standin.broker_protocol_conformance`
- Registered repair: `owner_standin.broker_protocol_reconcile`
- Resolver: expire stale ceremonies and sessions; revoke orphaned,
  session-revoked, expired, or overlong grants; require a fresh challenge.

Consumed challenges are never reset or recreated. A live adapter/deployment and
an end-to-end passkey canary remain required before parent AC2 can be marked
complete.

## Rollback

Revert the child PR. There is no live broker, passkey, provider credential,
session, deployment, or permission to revoke in this slice.
