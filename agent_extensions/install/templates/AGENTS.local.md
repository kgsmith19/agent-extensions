<!-- managed-by: agent-extensions -->
# Local Agent Overlay (gitignored)

This file is injected by agent-extensions (issue #53) and is additive: your
repo's own `AGENTS.md` stays the project authority and wins where the two
differ. This overlay carries the machine baseline that agents must obey here.

## Precedence

1. Current owner instruction.
2. Project instructions (`AGENTS.md`, `CLAUDE.md`, `GEMINI.md`, plans, issues).
3. Machine baseline: `~/AGENTS.md`.
4. Harness defaults.

If rules conflict, follow the higher source and mention the conflict once.

## Non-negotiables (enforced + expected)

- Read project instructions before changing files.
- Smallest correct change; edit canonical implementations in place; no parallel
  v2/fixed/final files.
- No hidden failures: no silent fallbacks, broad catches, fake success, skipped
  tests, or weakened assertions.
- Verification before completion: run the narrowest command that proves the
  claim, read the output, report command + result. Never claim success while
  checks, review, or acceptance remain unresolved.
- Bugs: find root cause before fixing (systematic debugging, no symptom patches).
- Behavior changes: tests first when practical; prove tests can fail.
- Isolation: non-trivial changes happen in a worktree
  (`.worktrees/issue-<n>-<slug>`), never directly on `main`.
- Never push to protected branches (`main`/`master`); work lands via PR.
- Never hard-delete config, credentials, keys, or unmerged work; archive first.

## Secrets and authority

- Never paste, print, commit, or log secret values. Secrets live in the
  configured secret provider (see `~/.config/agents/config.yaml`).
- Governed repo mutations use the `dev-agent` identity minted from the secret
  provider; owner credentials are break-glass only.
- The machine guard (`.claude/settings.local.json` → agent-extensions guard)
  blocks secret-pattern writes and protected-branch pushes at the tool layer.

## Session continuity

- The project capsule (`~/.agent-state/capsules/`) is refreshed on compaction
  and shutdown; a fresh session resumes from it without re-deriving context.
- Refresh handoff notes before context runs low; leave no stranded work.

## Customization

Local process notes go in gitignored fragments under `AGENTS.local.d/*.md`
(merged lexically by the installer). Re-run `ae init` to re-render this file
after template or fragment changes; hand edits without the managed marker are
preserved and reported, never overwritten.
