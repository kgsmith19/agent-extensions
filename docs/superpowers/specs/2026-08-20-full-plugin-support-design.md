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

Both translators live in the existing PowerShell/bash sync scripts. PowerShell
needs no new dependency — `ConvertFrom-Json`/`ConvertTo-Json` are native
cmdlets. Bash has no native JSON parser, and `jq` is not guaranteed present
(confirmed absent on Kyle's own dev machine while scoping this plan) —
hand-rolling a JSON parser in pure bash for arbitrary nested MCP configs
(nested `env` objects, `args` arrays, escaped strings) is exactly the
fragile-parsing risk this design's error-handling section warns against, so
`sync.sh` requires `jq` for the MCP-translation path specifically (not for
the rest of the script), checked at the start of that path with a loud,
actionable error if missing — never a silent skip. TOML *generation* (the
output side, for Codex) stays hand-rolled in both languages either way,
since it's simple flat output, not parsing.

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

---

## Amendment, 2026-08-27: plugin source resolution

Live verification of the implemented branch found the design's central
locating assumption to be wrong. This amendment supersedes the affected
parts of Architecture, Data flow, and Error handling above. Everything else
in this design stands.

### What live verification found

`sync.ps1` reported `Sync complete.` and exited 0 while linking 0 of 17
external skills, leaving `~/.codex/config.toml` byte-identical, and creating
39 Antigravity junctions whose targets were all empty directories.

Three defects, in increasing order of seriousness:

1. The implementation resolved a plugin to
   `<cache>/<marketplace>/<plugin>`. Nineteen of the declared plugins
   actually live at `<cache>/<marketplace>/plugins/<plugin>`.
2. Three more (`github`, `greptile`, `playwright`) live under
   `external_plugins/`, a second inline layout not accounted for anywhere.
3. Seventeen are not stored in the marketplace repository at all. They are
   separate git repositories referenced by URL. The entire
   `superpowers-marketplace` clone is `LICENSE` and `README.md`.

Every one of these failed silently: a plugin directory that is not found was
not an error, so the loop continued, collected no failure, and exited 0.

The tests did not catch this because the fixture marketplace places its
plugins at the repository root — a layout no real marketplace uses. Fixture
and implementation shared one wrong assumption, so a green suite proved only
that they agreed with each other.

### Corrected model: the manifest is the only authority

A plugin's location is read from its marketplace's own
`.claude-plugin/marketplace.json`. It is never inferred from a path
convention. Census of the 39 declared plugins confirms every one is
resolvable there, in one of two forms:

**Inline** — `source` is a string holding a repo-relative path (22 plugins:
19 under `./plugins/`, 3 under `./external_plugins/`). Resolve relative to
the marketplace clone root. The two directory names are incidental; any
relative path must work.

**External repository** — `source` is an object with `source: "url"` and a
`url`, plus optional `sha`, `ref`, and `path` (17 plugins). Clone it into
`.vendor-cache/_plugins/<marketplace>/<plugin>/`, separate from the
marketplace clones. `path`, when present, names a subdirectory of that clone
that is the plugin root (4 plugins). `ref` names a branch or tag (3).

### Pinning the unpinned

Seven external-repo plugins declare neither `sha` nor `ref`, so the
marketplace manifest alone cannot pin them and they would float to whatever
their default branch holds at sync time. That contradicts this repo's
reproducibility guarantee.

Resolution: **pin on first use.** On first sync of an unpinned plugin, the
resolved commit SHA is written back into
`bootstrap/external-marketplaces.json` as a `resolvedCommit` field alongside
the plugin name, and every later sync checks out that SHA. Advancing it is a
deliberate edit, consistent with the existing rule that re-pinning is manual
and nothing propagates automatically. A plugin that already declares `sha`
is pinned by it and never needs `resolvedCommit`.

This makes `external-marketplaces.json` a lockfile as well as a declaration.
The declaration is hand-edited; the resolved pins are machine-written. The
file records both, and a sync that writes a new pin says so on stdout.

### Error handling, corrected

A declared plugin that cannot be resolved is a **reported failure**, never a
skip. This covers: absent from the marketplace manifest, an inline path that
does not exist in the clone, a clone or checkout that fails, and a `path`
subdirectory that is missing. Consistent with this design's existing rule,
one plugin's failure must not abort the other 38, and any failure means a
non-zero exit.

A plugin that resolves successfully but ships no `skills/` and no
`.mcp.json` contributes nothing and is not a failure — unchanged from the
original design, and still distinct from "could not be found."

### Additional acceptance criteria

These are additive to the six above.

7. **Source-kind coverage.** Every one of the three source forms — inline
   path, external repo pinned by `sha`, external repo pinned by
   `resolvedCommit` — is exercised by the fixture, including an external-repo
   plugin using `path` to locate a subdirectory.
8. **No silent skips.** A declared plugin that cannot be resolved by any
   means produces a named failure on stdout and a non-zero exit. Verified by
   a fixture that declares a plugin absent from its manifest.
9. **Live, not fixture.** Criteria 2 and 3 are re-verified against the real
   39-plugin roster on a real machine, counting linked skills and
   `[mcp_servers.*]` entries against the count the manifests predict. A green
   test suite does not satisfy this criterion.
10. **Fixture realism.** The fixture marketplace reproduces the real
    directory layouts: plugins under `plugins/`, under `external_plugins/`,
    and referenced as external repositories.
