# agent-extensions: full-plugin support (Spec 1 of 2) design

Date: 2026-08-20
Status: approved for planning

## Problem

`agent-extensions` v1 proved cross-provider distribution for Skills only. Kyle's
actual day-to-day toolset is much larger: 35 plugins installed today across two
external, actively-maintained marketplaces (`anthropics/claude-plugins-official`,
`obra/superpowers-marketplace`) — commands, subagents, hooks, and MCP servers,
not just skills. None of that is declared anywhere; it exists only as ad hoc
local install state on one machine, which is exactly the failure mode
`agent-extensions` was built to eliminate (see the v1 design's problem
statement — cloud/remote sessions inherit none of it).

This spec makes that 35-plugin roster declarative and reproducible across
Claude Code, Codex, and Antigravity, for the two component types that port
cleanly: **Skills** (already proven) and **MCP servers** (new, mechanical
translation). Agents, hooks, and command-to-skill re-authoring are Spec 2 —
deliberately separated because they need per-provider judgment and format
translation (Codex agents are TOML with a `developer_instructions` field, not
a drop-in port), while this spec's content is mechanical and low-risk.

## Scope

**In scope:** all 35 plugins currently installed on Kyle's machine (recorded
today in `~/.claude/plugins/installed_plugins.json`), sourced from two
external marketplaces, for Skills and MCP servers only, across all three
providers.

**Out of scope (Spec 2, later):** Agents, Hooks, Commands. Commands will be
re-authored as skills (Codex deprecated its own command mechanism in favor of
skills; Antigravity has no separate command concept — a skill's name *is* its
slash command) — a content-authoring effort per plugin, not a mechanical
sync-script extension, so it's a separate spec.

## Non-goals

- Republishing `agent-extensions` itself as a native Codex/Antigravity
  marketplace (`.codex-plugin`/Antigravity `plugin.json` manifests already
  exist for this repo's own 2 plugins; extending that pattern to 35 vendored
  external plugins is future work, not required for this spec's acceptance
  criteria).
- Freezing Kyle's actual Claude Code plugin versions. Claude Code continues
  to install from the live marketplace repos, same as today — only the
  Codex/Antigravity vendor-cache is pinned (see Architecture).
- Full verification that Antigravity's plugin loader recognizes non-native
  plugin folders. This was already an open, unverified risk in the v1 design
  for this repo's own 2 plugins; this spec inherits and extends that same
  risk to 35 more plugins rather than resolving it (see Risks).

## Architecture

A new sync stage runs before the existing per-provider loops: declare the
external marketplaces this repo depends on, clone-and-pin them to a local
cache, then extend the existing Codex/Antigravity linking loops (and Claude
Code's native install) to also cover that content.

```
bootstrap/external-marketplaces.json     # NEW — declares dependencies
{
  "marketplaces": [
    { "name": "claude-plugins-official",
      "repo": "anthropics/claude-plugins-official",
      "pinnedCommit": "<sha>",
      "plugins": ["superpowers", "frontend-design", "code-simplifier", "..."] },
    { "name": "superpowers-marketplace",
      "repo": "obra/superpowers-marketplace",
      "pinnedCommit": "<sha>",
      "plugins": ["claude-session-driver", "episodic-memory", "..."] }
  ]
}

.vendor-cache/                            # NEW, gitignored — like .worktrees/
  claude-plugins-official/                # git clone, checked out to pinnedCommit
  superpowers-marketplace/
  _staged/<provider>/<plugin>/            # generated per-provider MCP configs,
                                           # derived from the clone, never
                                           # written back into it
```

**Why declare specific plugin names, not "every plugin in the marketplace":**
mirrors Kyle's actual roster (`installed_plugins.json`) — the repo should
declare what's used, not import an entire marketplace's contents
speculatively.

**Why pin the vendor-cache but let Claude Code float free:** Claude Code
already versions each installed plugin itself (`gitCommitSha` recorded per
plugin in `installed_plugins.json`) and Kyle actively wants those to stay
current — so Claude Code continues calling `claude plugin marketplace add`
and `claude plugin install` against the live repos, unchanged from today. The
vendor-cache pin exists solely so Codex and Antigravity, which have no
marketplace pointed at these two repos, get a reproducible source to link
from. Re-pinning (bumping `pinnedCommit`) is a manual, deliberate action —
same "nothing propagates automatically" philosophy as the existing
`VENDORED-FROM` skill pins, applied at marketplace granularity.

## Components (per provider)

- **Claude Code**: `claude plugin marketplace add <repo>` for each declared
  external marketplace, then `claude plugin install <plugin>@<marketplace>`
  for each declared plugin — scripting what Kyle already did by hand, now
  reproducible on any machine or cloud session.
- **Codex**: the existing skill-linking loop extends to also walk
  `.vendor-cache/<marketplace>/<plugin>/skills/*`, same junction/symlink
  pattern already proven for this repo's own plugins. New: an MCP translator
  reads each plugin's `.mcp.json` (where present), converts it to Codex's
  `[mcp_servers.<name>]` TOML table shape, and merges (never overwrites) the
  result into `~/.codex/config.toml`.
- **Antigravity**: the existing whole-folder-junction loop extends to also
  cover `.vendor-cache/<marketplace>/<plugin>`, plus the same MCP reshape
  targeting Antigravity's `mcp_config.json` format.

Both translators are hand-rolled in the existing PowerShell/bash sync
scripts — MCP server definitions are simple flat structures (command, args,
env, or url, headers), not general TOML/JSON, so no new runtime dependency
(Python/Node) is justified.

## Data flow

1. Read `external-marketplaces.json`.
2. For each marketplace: clone or fetch+checkout to `pinnedCommit` in
   `.vendor-cache/<marketplace>/` — skip (idempotent no-op) if already at
   that commit.
3. For each declared plugin, generate provider-specific MCP translations
   into `.vendor-cache/_staged/<provider>/<plugin>/` — derived output,
   never mutates the pinned clone.
4. Claude Code stage: marketplace-add + plugin-install, as today, extended
   to the declared external plugins.
5. Codex stage: link skills (from own `plugins/` and the vendor-cache) into
   `~/.agents/skills/`; merge translated MCP entries into
   `~/.codex/config.toml`.
6. Antigravity stage: junction plugin folders (own and vendor-cache) into
   `~/.gemini/config/plugins/<name>`; write translated `mcp_config.json`.

## Error handling

A single plugin's failure (clone error, malformed `.mcp.json`, an MCP field
the translator doesn't recognize) must not abort the other 34 — sync
collects failures per plugin, continues, prints every failure at the end,
and exits non-zero if any occurred. No silent drops: a server that fails to
translate is a reported failure, never a quietly-missing config entry.
Merges into `~/.codex/config.toml` must merge existing content, never
overwrite it wholesale — that file can carry unrelated hand-written entries.
This extends the existing "loud failure, never silent fallback" rule
(already applied to junction creation) to cloning and config-merging.

## Testing

Automated tests use a **local fixture git repo** standing in for "an
external marketplace" — a couple of fake plugins (one with a skill, one with
an MCP server, one with neither as a negative case) — so tests never depend
on live network/GitHub access. The MCP translator is built TDD-first against
its own unit tests: sample `.mcp.json` in, exact expected TOML (Codex) and
JSON (Antigravity) out, plus the no-servers case. The existing live-CLI test
(`sync.claude-code.test.ps1`, deliberately real against Kyle's machine, not
scratch-isolated — `claude plugin marketplace add` has no sandbox flag)
extends to register one real external marketplace and one real plugin,
verified via `claude plugin list`.

Acceptance criteria:

1. **Claude Code**: after sync, `claude plugin list` includes all 35
   external plugins as `<plugin>@<marketplace>`, enabled.
2. **Codex**: every skill from every plugin that ships one exists as a live
   link (not a copy) under `~/.agents/skills/` — plugins with no `skills/`
   directory (e.g. pure-MCP plugins like `postman`, `figma`) correctly
   contribute zero skill links, not a failure. `~/.codex/config.toml`
   contains a `[mcp_servers.*]` entry for every plugin that ships one, with
   correct command/args/env or url/headers.
3. **Antigravity**: every plugin folder is linked under
   `~/.gemini/config/plugins/<name>`; `mcp_config.json` reflects the same
   MCP servers as Codex's translation, reshaped to Antigravity's schema.
4. **Idempotency**: running sync twice produces no errors, no duplicate
   registrations, no broken links, and no vendor-cache re-clone when already
   at the pinned commit.
5. **Content integrity**: MCP translation is verified content-correct (not
   just "a file exists") for a representative sample covering stdio
   transport, HTTP transport, and the zero-MCP-servers case.
6. **Partial-failure isolation**: a deliberately malformed plugin fixture
   causes sync to report that one failure and exit non-zero, while every
   other plugin still syncs successfully.

## Risks

- Whether Antigravity's plugin loader actually reads non-native plugin
  folders (no Antigravity-native `plugin.json`) is unverified — inherited
  from the v1 design's same open risk, now covering 35 plugins instead of 2.
  Flagged for a real verification pass, not assumed.
- A force-push or history rewrite on either upstream marketplace could
  invalidate a `pinnedCommit`. Handled by the loud-failure rule — sync fails
  clearly and names the missing commit, never silently falls back to a
  different ref.
- The MCP translator only needs to understand the field shapes seen across
  the 35 plugins' current `.mcp.json` files. An unrecognized future field is
  a reported translation failure for that plugin, not a silent drop —
  consistent with the error-handling section above.

## Deferred to Spec 2

- Agents (Codex TOML transform, Antigravity near-native port).
- Hooks (event vocab overlaps across all three providers; needs its own
  design pass).
- Commands re-authored as skills for Codex/Antigravity (full re-authoring
  effort across all 35 plugins' commands, per Kyle's explicit scope choice —
  not a mechanical translation).
