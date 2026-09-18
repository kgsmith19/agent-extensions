# Sibling Contract Binding (agent-extensions Half) v1.0.0

## Binding

Merged agent-engineering-standard SHA: `ad3c6c5f1512f4406087b499ca4c37e7c39dc1ea`
Merge date: `2026-09-18`
Standard contract file: `SIBLING_CONTRACT_BINDING_v1_0_0.md`
Standard contract version: `v1.0.0`

## Standard-Side Contract Requirements

Source: agent-engineering-standard `SIBLING_CONTRACT_BINDING_v1_0_0.md` (Stage 15a, merged as `ad3c6c5`).

### 1. Semantic Capability ID Format and Source

Capability IDs use the v5 format (264-element registry from Stage 5) and are referenced verbatim by catalog items.
Source: `Canonical/capabilities.json` (Stage 5 registry); contract field: `capability_namespace`.

### 2. Pin Format (Git SHA, Version Tag, Checksum)

Pins use full 40-hex git commit SHAs; version tags follow semver; rendered profiles carry SHA-256 checksums.
Source: `tools/sibling_contract.py`, `sibling_min_commit` field.

### 3. License Metadata Required Per Capability

Every capability entry includes license metadata. Binding fails closed on any entry lacking a well-formed license field.
Source: `Canonical/capabilities.json` schema; enforced by `validate_contract()` in `tools/sibling_contract.py`.

### 4. Provider Capability Manifest Format

Provider manifests use a standardized JSON schema with required fields: `provider`, `version`, `capabilities[]`, and `checksum`.
`check_binding()` verifies every Standard registry ID is covered by at least one agent-extensions catalog manifest entry.
Source: `tools/sibling_contract.py`, `check_binding()`; field `provider_capability_manifest`.

### 5. Contract Version and Compatibility Guarantees

Contract version is semver (X.Y.Z); changes are additive-only (new optional fields allowed; renaming, removal, or type change requires a major bump).
Current version: `1.0.0` (`CONTRACT_VERSION` in `tools/sibling_contract.py`).
Source: `CONTRACT_VERSION`, `compatibility` field; validation in `validate_contract()` and `check_binding()`.

### Additional Binding Rules

- **Offline Bootstrap:** contract + registry + schemas suffice with no live network.
- **Failure Behavior:** fail closed on skew (unknown version, hash mismatch, absent coverage) with repair guidance; no silent fallback.
- **Sibling Repo:** canonical sibling is `kgsmith19/agent-extensions`; no substitution.

## Agent-Extensions Implementation

### Schemas Implemented

1. **CatalogEntry / Catalog** (`agent_extensions/schemas/catalog_schema.py`)
   - Validates semantic ID format (`capability.*`)
   - Enforces duplicate ID detection
   - Provides catalog lookup by semantic ID

2. **ProfileEntry / Profile** (`agent_extensions/schemas/profile_schema.py`)
   - Lifecycle state machine: AVAILABLE → STAGED → ENABLED → RESIDENT → INVOKED → {VERIFIED, DEGRADED}
   - Validates valid state transitions
   - Counts extensions by lifecycle state

3. **LicenseMetadata / ProvenanceRecord** (`agent_extensions/schemas/license_metadata.py`)
   - SPDX license validation
   - Source URL and commit SHA tracking
   - Preserves existing extension pins during migration

4. **ContractBinding / ContractValidator** (`agent_extensions/schemas/contract_v1_0_0.py`)
   - Records exact Standard merged SHA
   - Tracks schema versions
   - Validates version/SHA compatibility

### Sync Implementation

- **read_back.py**: Reconstruct catalog/profile from filesystem JSON, offline bootstrap
- **render.py**: Render profile to provider-specific manifests, validate profile against catalog

### Compatibility Matrix

| Standard Schema | Agent-Extensions Schema | Version | Status |
|---|---|---|---|
| Semantic Capability ID | `semantic_id` (string, `capability.*` format) | 1.0.0 | ✓ Matching |
| Provider Manifest | Provider Binding (string, mapped from semantic ID) | 1.0.0 | ✓ Compatible |
| Enforcement Class | Lifecycle State (enum, AVAILABLE→...→VERIFIED/DEGRADED) | 1.0.0 | ✓ Compatible |
| Context/Tool Budget | Profile Entry count (countable by state) | 1.0.0 | ✓ Compatible |
| License Metadata | SPDX ID + Provenance (source URL, commit SHA) | 1.0.0 | ✓ Compatible |

## Binding Proof

- Schemas defined: ✓
- Tests passing: ✓ (29 passed, see Verification Results)
- Offline bootstrap verified: ✓ (`test_offline_bootstrap`)
- Migration preserves pins: ✓ (`test_license_metadata_preserves_existing_pins`)
- Provider bindings implemented: ✓ (`test_apply_provider_binding_*`)

**Status: READY FOR MERGE**

## Verification Results

**Test Run:** 2026-09-18 (branch `stage-15b-sibling-contract-agent-extensions`)

### Test Summary

```text
29 passed in 0.20s
```

### Coverage

- Catalog schema: 4 tests ✓
- Profile schema: 7 tests ✓
- License metadata: 3 tests ✓
- Contract binding: 7 tests ✓
- Sync (read-back/render): 8 tests ✓

**Total: 29 tests, all PASS**
