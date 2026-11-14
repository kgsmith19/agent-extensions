# Owner Stand-In Authority Contract Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Publish a secret-free, versioned Owner Stand-In authority contract and threat model that future broker, capsule, bootstrap, and recovery work can consume.

**Architecture:** A strict Pydantic model is the executable semantic boundary, a checked-in JSON Schema is its portable wire contract, and one canonical JSON instance records the approved role and provider aliases. A human-readable threat model explains trust boundaries and rollback while explicitly granting no authority.

**Tech Stack:** Python 3, Pydantic v2, JSON Schema Draft 2020-12, pytest, Markdown.

**Spec:** GitHub Issue `kgsmith19/agent-extensions#109`, child of canonical outcome `#98`.

## Global Constraints

- Store identifiers, aliases, versions, and digests only; never store a secret or PII value.
- The contract grants no live privilege and creates no second broker, vault, bootstrap, queue, or policy root.
- Unknown schema versions, extra fields, duplicate provider mappings, role escalation, missing takeover/rollback, and secret-like strings fail closed.
- `owner-standin` is the sole non-human owner-equivalent role; ordinary roles stay explicitly bounded.
- Future slices bind the canonical JSON bytes by `sha256:<64 lowercase hex>`.

## Review Focus

- Unicode or control characters in aliases must be rejected, not normalized into a different identity.
- A provider added twice under different casing must be treated as a duplicate.
- Secret-like material nested in any future string field must still be caught by the recursive boundary scan.
- An empty ordinary-role list must fail because separation cannot be proved.
- A valid-looking digest with uppercase hex must fail to preserve one canonical representation.

---

### Task 1: Executable contract, canonical instance, and threat model

**Files:**
- Create: `agent_extensions/schemas/owner_standin_v1.py`
- Create: `agent_extensions/schemas/owner_standin_v1.schema.json`
- Create: `agent_extensions/contracts/owner_standin_v1.json`
- Create: `docs/OWNER_STANDIN_THREAT_MODEL.md`
- Create: `tests/test_owner_standin_contract.py`
- Modify: `README.md`

**Interfaces:**
- Consumes: Pydantic v2 strict models and `agent_extensions.schemas.extension_lock.compute_digest`.
- Produces: `OwnerStandInContract`, `load_contract(path)`, `canonical_json(contract)`, and `contract_digest(contract)` for future signed capsules and receipts.

- [ ] **Step 1: Write failing tests for the contract boundary**

Add tests proving the canonical instance loads, the generated JSON Schema matches the checked-in schema, and unknown versions, extra fields, duplicate/case-variant providers, owner authority on ordinary roles, missing recovery data, secret-like nested strings, control characters, empty ordinary roles, and uppercase digests are rejected.

- [ ] **Step 2: Run the focused tests and verify RED**

Run: `python -m pytest -q tests/test_owner_standin_contract.py`

Expected: collection fails because `agent_extensions.schemas.owner_standin_v1` does not exist.

- [ ] **Step 3: Implement the strict model and canonical artifacts**

Implement `OwnerStandInContract` with `extra="forbid"`, exact version `1.0.0`, ASCII slug fields, unique case-insensitive provider mappings, a non-empty bounded ordinary-role set, explicit independent-human takeover and rollback fields, and a recursive secret/PII-shaped string rejection pass. Generate and check in its Draft 2020-12 JSON Schema and a canonical secret-free instance covering GitHub, Infisical, Google, cloud, and browser provider aliases.

- [ ] **Step 4: Publish the threat model and repository route**

Document assets, trust boundaries, attacker classes, replay/forgery, confused deputy, exfiltration, poisoned bootstrap, provider compromise, browser theft, cloud outage, recovery loss, detectors, containment, takeover, and rollback. Point README readers to `#98` as the only canonical outcome and state that the contract grants no authority.

- [ ] **Step 5: Run focused and full verification**

Run: `python -m pytest -q tests/test_owner_standin_contract.py`

Expected: all focused tests pass.

Run: `python -m pytest -q`

Expected: the full suite passes with zero failures.

Run: `python .github/ci/script_tests.py sh && python .github/ci/script_tests.py ps1`

Expected: both script gates pass.

- [ ] **Step 6: Commit**

Commit message: `feat: add owner stand-in authority contract (#109)`
