# agent-extensions v1 design

Date: 2026-08-20
Status: approved for planning

## Problem

Kyle's Claude Skills, plugins, and MCP servers currently live scattered across
machine-local directories (`~/.claude/skills`, `~/.agents/skills`,
`~/.gemini/config/skills`, individual plugin caches) with no single source of
truth. Content added on one machine or in one provider's config doesn't reach
any other environment — including Claude Code cloud/remote sessions, which
inherit almost nothing from a user's local machine (see prior investigation
this session: only repo-committed `.claude/` content and skills enabled on
the claude.ai account carry over).

`agent-extensions` is a dedicated repo holding Kyle's personal skills (and
later plugins/MCP servers), distributed so that Claude Code, Codex, and
Antigravity — locally and in cloud/remote sessions — can all load the same
content from one place, with no environment carrying state the repo doesn't
have.

## Scope

**v1 covers Skills only.** Plugin-provided MCP servers and hooks are an
explicit non-goal for this spec (see below) — v1 proves the cross-provider
skill-distribution pattern before layering anything more on it.

**v1 content is the 3 skills currently enabled on Kyle's claude.ai account**
(confirmed via Settings > Skills, 2026-08-20): `canvas-design`,
`skill-creator`, `web-artifacts-builder`. All three exist as Apache
2.0-licensed folders in Anthropic's public `github.com/anthropics/skills`
repo, confirmed by directory listing the same day. Content is vendored
(copied) from there at a pinned commit, not live-linked — this is a one-time
copy Kyle can re-sync later if Anthropic updates the originals, not a git
submodule. The source commit SHA is recorded in a `VENDORED-FROM` file at
each plugin's `skills/` root, one line per skill: `<skill-name> <commit-sha>
<date>` — that's what a future re-sync diffs against.

## Non-goals for v1

- MCP servers, hooks, custom agents/commands — a later spec, once the skill
  pattern is proven.
- Merging with `agent-engineering-standard` — that repo is a process/
  governance standard (Issues → branch → PR Gate → merge), distributed by
  rendering into consuming repos via `standardctl.py`. `agent-extensions` is
  a capability bundle, distributed by marketplace/plugin install at the
  runtime level. Different mechanism, different audience — decided as
  separate sibling repos, out of scope to revisit here.
- Full behavioral parity for `canvas-design` and `web-artifacts-builder` on
  Codex or Antigravity. Both skills instruct Claude to use Claude.ai's Canvas
  and Artifacts surfaces, which don't exist on those providers. They are
  expected to be present but inert outside Claude Code/claude.ai — this is
  intentional, not a defect to fix in v1.

## Architecture

Two plugins under one marketplace, reflecting the actual portability of their
content rather than pretending everything is equally cross-provider:

- **`anthropic-product-skills`** — `canvas-design`, `web-artifacts-builder`.
  Claude-surface-specific by nature.
- **`general-skills`** — `skill-creator`. A meta-skill for authoring new
  skills; genuinely useful on any provider.

Repo layout (mirrors the reference pattern in the installed `superpowers`
plugin — one canonical `skills/` per plugin, thin per-harness manifests
pointing at the same files, no duplication):

```
agent-extensions/
├── plugins/
│   ├── anthropic-product-skills/
│   │   ├── .claude-plugin/plugin.json
│   │   ├── plugin.json                 # Antigravity manifest
│   │   ├── .codex-plugin/plugin.json
│   │   └── skills/
│   │       ├── canvas-design/          # vendored from anthropics/skills
│   │       └── web-artifacts-builder/  # vendored from anthropics/skills
│   └── general-skills/
│       ├── .claude-plugin/plugin.json
│       ├── plugin.json
│       ├── .codex-plugin/plugin.json
│       └── skills/
│           └── skill-creator/          # vendored from anthropics/skills
├── .claude-plugin/marketplace.json     # lists both plugins
├── bootstrap/
│   ├── sync.ps1                        # Windows install/link
│   └── sync.sh                         # Unix/cloud-container install/link
├── LICENSE                             # Apache 2.0 (matches vendored content)
├── AGENTS.md
├── CLAUDE.md                           # import-only pointer to AGENTS.md
├── GEMINI.md                           # import-only pointer to AGENTS.md
├── docs/superpowers/specs/             # this file and future specs
└── README.md
```

No absolute local paths are committed anywhere in this repo. Every path the
sync script touches on a user's machine is derived at run time from
`$HOME`/`$env:USERPROFILE`, never hardcoded — the same discipline applied to
the guards-config fix earlier this session.

## Sync mechanism

`bootstrap/sync.ps1` (Windows, primary) and `bootstrap/sync.sh` (Unix/cloud
containers) perform the same three actions, one per provider:

| Provider | Action |
|---|---|
| Claude Code | `claude plugin marketplace add <repo>` (local path or GitHub URL), then `claude plugin install <plugin>@agent-extensions` for each of the two plugins |
| Codex | junction (Windows) / symlink (Unix) each plugin's `skills/*` into `~/.agents/skills/<skill-name>` — the neutral directory Codex reads by default |
| Antigravity | junction/symlink each plugin folder into `~/.gemini/config/plugins/<plugin-name>` |

The script is idempotent — safe to re-run after any content edit, and safe
to run when some or all links already exist. Windows directory junctions
(`mklink /J`) don't normally require elevation — that's part of why the
design uses junctions rather than symlinks — but a restricted environment
(policy, EDR/antivirus, a read-only volume) can still block creation. Any
such failure must be loud and actionable, never a silent no-op or placeholder
success.

## Distribution

Pushed to GitHub now as `kgsmith19/agent-extensions`, **private** by default
— this is personal tooling, and Kyle already has a working private-repo
cloud-session pattern (`gh auth login --with-token` from the vault; see
[[feedback-github-pat-cloud-session]]). Flip to public later if cloud-session
friction from privacy actually appears; no reason to default to public now.

## Verification

Since this spec ships infrastructure (a sync script with real logic: junction
creation, marketplace registration, idempotency), not application business
logic, "tests" here mean an automated check that each provider actually picks
up each plugin after `sync.ps1` runs — not a manual eyeball pass.

Acceptance criteria, one per provider, each independently checkable:

1. **Claude Code**: after `sync.ps1`, `claude plugin list` includes both
   `anthropic-product-skills@agent-extensions` and
   `general-skills@agent-extensions` as enabled.
2. **Codex**: `~/.agents/skills/canvas-design/SKILL.md`,
   `~/.agents/skills/web-artifacts-builder/SKILL.md`, and
   `~/.agents/skills/skill-creator/SKILL.md` all exist and are junctions/
   symlinks resolving into this repo's `plugins/*/skills/*`, not copies —
   editing the repo file must change what Codex reads without re-running
   sync.
3. **Antigravity**: `~/.gemini/config/plugins/anthropic-product-skills` and
   `~/.gemini/config/plugins/general-skills` exist as junctions/symlinks into
   this repo's `plugins/*`, same live-link requirement as above.
4. **Idempotency**: running `sync.ps1` twice in a row produces no errors, no
   duplicate marketplace/plugin registrations, and no broken links.
5. **Content integrity**: each vendored `SKILL.md`'s frontmatter `name` field
   matches its folder name, and the original Apache 2.0 LICENSE/attribution
   from `anthropics/skills` is preserved.

These map to `bootstrap/sync.test.ps1` (Windows) — written first, per TDD,
against a scratch `$env:USERPROFILE`/Claude-config override so the test
never touches Kyle's real machine state.

## Risks

- A restricted environment can still block junction creation even without
  elevation being the cause — handled by the loud-failure rule above, not a
  silent fallback.
- A pinned-commit vendor copy means Anthropic updates to these 3 skills
  don't propagate automatically. Accepted for v1; re-sync is a manual,
  deliberate action, consistent with "nothing propagates automatically"
  being the same distribution philosophy `agent-engineering-standard`
  already uses.

## Deferred to later specs

- MCP server and hook distribution through the same marketplace.
- Additional themed plugins as new personal skills are authored.
- Reconsidering public visibility if cloud-session friction appears.
