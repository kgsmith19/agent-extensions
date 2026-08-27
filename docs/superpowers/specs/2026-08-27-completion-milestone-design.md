# agent-extensions completion milestone design

Date: 2026-08-27
Status: approved for planning

## Problem

`agent-extensions` set out to be the single declarative source for Kyle's
agent capabilities across Claude Code, Codex, and Antigravity, on every
device he works from. Two of its three layers exist. The third does not, and
the second has never been verified in the environment it was written for.

Concretely, as of this date:

- **v1 (landed on `main`)** distributes Skills only: 3 vendored skills in 2
  own plugins, linked into Codex (`~/.agents/skills`, confirmed live: 3
  links) and Antigravity (`~/.gemini/config/plugins`).
- **Spec 1 (implemented, unlanded)** extends that to 39 external plugins
  across 2 marketplaces, for Skills and MCP servers. Branch
  `worktree-full-plugin-support`, 15 commits, Tasks 1-11 all committed.
  Task 11 has no completion report: no final whole-branch review has run,
  and none of that design's 6 acceptance criteria have been checked live.
- **Nothing** addresses claude.ai account Skills, Connectors, or any surface
  without a filesystem. `~/.claude.json` declares zero user-level MCP
  servers; every MCP in daily use arrives via plugin or claude.ai connector,
  none of it declared by this repo.
- Two verifications have never happened: `sync.sh` on a real Unix target,
  and whether Antigravity's loader reads plugin folders that carry no
  Antigravity-native manifest.
- The GitHub repo is private and 3 commits behind local `main`, so no fresh
  machine or cloud sandbox can bootstrap from it.

## Scope

Everything required for the declared roster to reach every provider Kyle
uses (Anthropic, OpenAI/Codex, Google/Antigravity) on every surface he uses
(local Windows, local Unix, cloud sandbox, remote container, claude.ai web,
claude.ai mobile), plus a standing check that the declaration still matches
reality.

Delivered as five thin specs, not one. Each gets its own design, plan, and
worktree.

## Non-goals

- **Hand re-authoring commands as skills.** Roughly a hundred commands
  across 39 plugins, re-forking on every upstream update. Commands that do
  not port mechanically become a declared gap, reported per plugin, not a
  content project.
- **Automating the claude.ai UI.** There is no public API for account-level
  Skill management (upload, update, and delete are web-UI only) and
  connectors are configured through claude.ai settings. Browser automation
  was considered and rejected: it breaks on any UI change and cannot run
  headless, which is precisely where it would be needed.
- **Freezing Claude Code plugin versions.** Unchanged from Spec 1: Claude
  Code keeps installing from the live marketplaces; only the
  Codex/Antigravity vendor-cache is pinned.

## Architecture

One bootstrap entrypoint, capability-gated. `bootstrap/sync` detects which
surfaces exist on the current machine -- a `claude` CLI, a `~/.codex`, a
`~/.gemini`, a writable link target -- and runs only those stages. Every
skipped stage prints which stage and why. Cloud is not a special case: it is
the same script, fetched from the now-public repo, that happens to detect
fewer surfaces.

Rejected alternatives: separate per-environment scripts, which duplicate the
translation logic three ways and drift apart silently; and Claude Code's
cloud dotfiles mechanism, which is Claude-Code-only and gives Codex and
Antigravity nothing.

The surface without a filesystem gets the only mechanism available to it: a
declarative manifest, generated upload-ready bundles, and a printed
checklist. Sync says plainly what it cannot do and hands over the steps.

## Specs

### Spec 1 -- Land full-plugin support

A completion task, not a redesign. The design at
`docs/superpowers/specs/2026-08-20-full-plugin-support-design.md` stands.
Remaining: final whole-branch review, live verification of that design's 6
acceptance criteria, triage of the deferred minors recorded in the SDD
ledger, merge, push.

Blocks all four other specs, which build on its vendor-cache and translator
layer.

### Spec 2 -- Public distribution and universal bootstrap

Secret-scan the repo, make it public, push. A one-line bootstrap that works
on a bare machine or cloud sandbox holding no prior state. Capability
detection with explicit per-stage skip reporting.

Carries the two outstanding verifications, because this is the spec whose
acceptance depends on them: `sync.sh` exercised on a real Unix target, and
Antigravity's loader confirmed to read these plugin folders.

### Spec 3 -- claude.ai account surface

An `account-manifest.json` declaring which skills and connectors belong on
the claude.ai account. Sync builds upload-ready skill bundles into a staging
directory and prints an ordered add/remove checklist. Because no read API
exists, drift is detected against a recorded last-applied state rather than
by querying the account.

This is the only path by which claude.ai web and mobile receive anything.

### Spec 4 -- Agents and hooks mechanical port

Agents translated from Claude markdown to Codex TOML and to Antigravity.
Hooks ported where the three event vocabularies genuinely overlap. Commands
produce a generated per-plugin gap report naming what does not exist on each
provider.

Supersedes the "Spec 2" deferred by the full-plugin-support design, rescoped
to mechanical translation only.

### Spec 5 -- Drift and health verification

One `verify` command: declared roster against actual state on every provider
and surface, non-zero exit on any drift.

Each prior spec's acceptance criteria are verified once, at merge. This
converts them into a repeatable invariant, which is what stops the repo
decaying back into undeclared local state. It comes last because it can only
assert what the other four made declarative.

## Sequencing

Spec 1 lands first, then Spec 2, then Specs 3 and 4 concurrently, then
Spec 5.

Specs 3 and 4 each add a stage to the bootstrap entrypoint, so they cannot
start before Spec 2 has defined what a stage is: its capability check, its
failure-collection contract, and its skip-reporting format. Once that
interface exists they are genuinely disjoint -- Spec 3 owns a new account
module, Spec 4 owns the translator layer -- and run in separate worktrees,
each adding one registration to the entrypoint.

Spec 5 closes the milestone.

## Milestone acceptance

The milestone is complete when, from a machine holding no prior agent state:

1. One command bootstraps the full declared roster into Claude Code, Codex,
   and Antigravity, or reports precisely which surfaces were absent.
2. The same command run in a cloud sandbox reaches the same declared state
   for every surface that sandbox has.
3. `verify` exits zero on a synced machine and non-zero, naming the
   difference, after any declared item is removed by hand.
4. The claude.ai checklist names every account skill and connector in the
   manifest, and drift against last-applied state is reported.
5. Every provider-surface pair is either verified working or recorded as a
   declared gap with a reason. No pair is left assumed.

## Risks

- **Antigravity loader behaviour is still unverified** across 39 plugins,
  inherited unresolved from both prior designs. Spec 2 resolves it or
  records it as a declared gap; it is not carried forward a third time.
- **Making the repo public** requires that no secret has ever been committed.
  A history scan, not a working-tree scan, gates the flip.
- **The claude.ai surface has no read API**, so drift detection there is
  inferential. A skill deleted through the UI is invisible to `verify` until
  the next manual reconciliation. Stated as a limitation, not engineered
  around.
