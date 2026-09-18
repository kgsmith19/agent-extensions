# Surface Support — Automated vs Declared Gaps (Stage 24)

Extension Supply is complete through Stage 23. This table publishes
exactly which surfaces bootstrap automates and which stay honest gaps.
No false full-sync claim: anything without a public API is declared,
never silently assumed complete.

## Mode table

| Surface | Mode | Mechanism |
|---|---|---|
| claude-code | automated | marketplace install; hooks native |
| codex | automated | skill junctions + hook_env_wrapper |
| antigravity | automated | plugin junctions + hook_env_wrapper |
| local | automated | read SKILL.md direct (headless) |
| cloud-container | automated | scratch-HOME bootstrap (proven in tests) |
| claude-web | generated-manual | person uploads bundle via CHECKLIST.md |
| claude-mobile | generated-manual | person uploads bundle via CHECKLIST.md |
| web | unsupported | no public API — declared gap |
| mobile-api | unsupported | no public API — declared gap |

## Manual bundles (no-filesystem surfaces)

`generate_manual_bundle(repo, surface, dest)` stages real file copies
(never symlinks), secret-scans every file before distribution, writes
`CHECKLIST.md` with the bundle digest, and records `connectors: none
declared` explicitly when the roster has no connectors. After the person
confirms upload, `record_last_applied` persists the roster; roster drift
since then reads as stale via `is_stale_last_applied` / `diff_manual_state`.

Account skill/connector profiles stay declared, never resident: they are
not auto-loaded into ordinary coding sessions (per Non-Goals).

## Conformance consumption

Stage 23's `run_conformance` verdicts feed this table: a DEGRADED adapter
drops its surface to supervised-only until the canary goes green. The
web/mobile read-back gap stays DEGRADED-by-design until a public API
exists — browser automation of account UI is excluded by default.
