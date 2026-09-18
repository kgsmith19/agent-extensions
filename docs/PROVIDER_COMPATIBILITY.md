# Provider Compatibility Table (Stage 19)

One canonical capability set renders to four provider manifests with
identical checksums — adapters never add, drop, or rename entries.

Regenerate: build the 3-skill catalog via `migrate_marketplace`,
`select_for_task` over all IDs, then `render_all_providers(ids)`.
Fixtures: `generated/provider-<name>.json` (scratch, not committed).

## Capability preservation

| Provider | Capabilities | Checksum |
|---|---|---|
| claude | capability.canvas-design, capability.skill-creator, capability.web-artifacts-builder | `sha256:f2e075af094de53ed8568a1d0a688548d9b9f3bf3bde93803496f0c7621c56f2` (identical all four) |
| codex | same 3 | identical |
| antigravity | same 3 | identical |
| local | same 3 | identical |

(Ellipsis truncates; full digests live in the generated fixtures.)

## Per-surface differences (only these)

| Provider | MCP transport key | Unsupported (declared) | Import / pointer |
|---|---|---|---|
| claude | `url` | (none — native hooks) | `marketplace add agent-extensions; install <plugin>` |
| codex | `serverUrl` | `hook-inheritance`, `sandbox` | link `plugins/<plugin>/skills/<skill>` under `$CODEX_SKILLS_DIR` |
| antigravity | `serverUrl` | `hook-inheritance`, `sandbox` | link `plugins/<plugin>` under `$ANTIGRAVITY_PLUGINS_DIR` |
| local | `url` | `account-surface`, `sandbox` | read `plugins/<plugin>/skills/<skill>/SKILL.md` directly (headless) |

## Hook bridging

Non-Claude providers route hooks via `bootstrap/hook_env_wrapper.py`
(which sets `CLAUDE_PLUGIN_ROOT` and translates the JSON payload).
Hook inheritance is declared unsupported on codex/antigravity — the
wrapper bridges invocation, it does not inherit Claude-native hook
semantics. Bulk porting of agent/hook/command definitions is Stage 22;
live conformance canaries are Stage 23; cloud/mobile/web bundles are
Stage 24.
