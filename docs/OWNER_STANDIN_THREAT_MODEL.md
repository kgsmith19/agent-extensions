# Owner Stand-In identity and authority contract v1

Canonical outcome: [`agent-extensions#98`](https://github.com/kgsmith19/agent-extensions/issues/98). Thin delivery issue: `#109`.

This document and `owner_standin_v1.json` define policy metadata. This slice **grants no authority**, creates no account, mints no credential, and does not make a harness READY. The broker, signed capsule, provider grants, browser environment, live canaries, and recovery exercise remain later independently verified slices of #98.

## Identity and boundaries

`owner-standin` is one logical non-human identity with owner-equivalent policy intent. Provider-native principals are aliases beneath that identity, not new LifeOS roles. `physical-owner` is independent and retains Kyle takeover. `ai-coding-harness`, developer, reviewer, CI, and deploy are ordinary bounded roles and cannot mint an Owner Stand-In session.

The versioned JSON contract is the policy boundary. It stores only aliases, names, versions, and digests. Secret or PII values are forbidden. Future signed capsules bind this exact contract digest to a session; future brokers deliver any required value only to its target process or provider destination. Model context, generated prompts, receipts, queue state, and ordinary logs remain outside the secret boundary.

Trust boundaries are: physical-owner recovery; the future WebAuthn broker; the signed bootstrap capsule; the local/cloud harness; provider adapters; target child processes; the encrypted browser profile; Infisical; and each external provider. Crossing a boundary requires verification and read-back, not an instruction alone.

## Provider mapping

| Provider alias | Principal alias | Intended authority class | Secret/PII value stored here |
| --- | --- | --- | --- |
| GitHub | `github-owner-standin` | repository and identity administration | No |
| Infisical | `infisical-owner-standin` | secrets and identity administration | No |
| Google | `google-owner-standin` | workspace and identity administration | No |
| Cloud | `cloud-owner-standin` | cloud and identity administration | No |
| Browser | `owner-standin-browser` | browser-session and recovery operation | No |

The mapping records required capability classes, not evidence that a live grant exists. Live provider read-back is mandatory before a later slice may report READY.

## Threats and required controls

| Threat | Failure to prevent | Required detector / containment |
| --- | --- | --- |
| Challenge replay or forgery | Reuse or fabrication of an authentication ceremony | Single-use, origin/RP-bound WebAuthn challenge; reject consumed, expired, wrong-origin, and wrong-RP challenges. |
| Confused deputy | A bounded agent causes the broker to perform owner work outside its task | Bind identity, role, task, audience, capability, expiry, and expected state into the grant and receipt. |
| Credential exfiltration | A secret reaches model context, prompts, config, queue state, receipts, or logs | Target-process injection, mutation tests, output redaction, bounded cache, and immediate revocation. |
| Poisoned bootstrap | A fresh environment loads altered policy or tools | Verify contract, standard, source, adapter, and capsule digests before privilege; fail closed on mismatch. |
| Provider compromise | One provider becomes an unauthorized policy root | Independent provider mappings, shortest practical sessions, provider read-back, and physical-owner recovery outside the Stand-In environment. |
| Browser-session theft | An encrypted persistent profile is copied or reused elsewhere | Device/session binding, encrypted storage, expiry, provider logout/revocation, and auditable session replacement. |
| Cloud outage | The normal broker or workspace is unreachable | Tested local recovery workspace restores the same policy root; it does not fork policy. |
| Recovery loss | The Stand-In can disable the only takeover path | Independently held physical-owner recovery, periodic restore test, and recovery evidence stored outside Stand-In control. |
| Capsule downgrade | A valid older policy is replayed | Exact version/digest binding, maximum age, monotonic policy revision, and stale-capsule rejection. |
| Ordinary-role escalation | CI, deploy, or coding harness inherits owner-equivalent access | Explicit role ceilings, deny tests, separate principals, and no ordinary role able to mint owner sessions. |

## Kyle takeover and rollback

Kyle takeover uses the independent `physical-owner` path. The recovery runbook must: trigger the global kill switch, revoke every Stand-In session, disable the broker role, revoke or rotate affected provider sessions, verify provider-native denial, restore ordinary scoped roles, and recover through `local-recovery-workspace`. It must not depend on the Stand-In browser, cloud workspace, or secrets it is revoking.

Rollback for this AC1 slice is a repository revert: remove or revert the contract, schema, tests, and this document. Because this slice creates no live grant, existing identities and provider permissions remain unchanged. Rollback for later privileged slices is the #98 R3 procedure: disable the role and broker, revoke sessions and provider grants, return ordinary roles to their prior scoped credentials, and use the independent recovery path.

## Verification contract

A future slice may consume v1 only after it verifies the JSON Schema, semantic invariants, canonical contract digest, canonical outcome `#98`, source revisions, and `grants_live_authority: false`. Unknown versions, extra fields, duplicate provider aliases, missing takeover/rollback data, secret/PII-shaped strings, or owner-equivalent ordinary roles are BLOCKED—not warnings.
