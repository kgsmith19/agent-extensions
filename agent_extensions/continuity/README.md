# Provider-neutral continuity

Session continuity that belongs to no provider. One neutral store, one CLI,
thin per-harness adapters. Nothing in the core imports or assumes Claude,
Codex, Antigravity, or pi.

## Why

Every harness clears or compacts context differently, and each one's memory
feature is a vendor feature. This keeps the *record of where work stopped*
in a plain JSON capsule under `~/.agent-state/capsules/` and lets any harness
read it at startup and refresh it before compaction.

## Capsule

A capsule is a small JSON record: task, phase, branch/head, next action,
blockers, decisions, evidence, open issues, harness/model, timestamp. Auto
fields (git state, harness, model, time) are refreshed on every capture;
manual fields persist until overwritten.

## Local overlay (uncommitted)

`<project>/AGENTS.local.md` is a gitignored, repo-local overlay for process you
cannot commit — for example on a team repository. It is **additive**: unlike
`AGENTS.override.md` or `CLAUDE.local.md`, which *shadow* a committed `AGENTS.md`,
this file is only ever injected alongside it, so a team's own `AGENTS.md` keeps
governing.

`render` outputs the overlay first, then the capsule, so the same session-start
injection that carries continuity also carries your local rules. Bounded at
32 KiB. Add `AGENTS.local.md` to `.gitignore` (the standard's fragment already
does).

## CLI

```bash
# refresh auto fields + set the manual fields you know
python agent_extensions/continuity/adapters/capsule.py capture \
  --task "..." --phase IMPLEMENT --next "..." --blocker "..." --issue 47

# refresh auto fields only (never clobbers manual fields)
python agent_extensions/continuity/adapters/capsule.py capture --auto

# print for injection
python agent_extensions/continuity/adapters/capsule.py render --format plain
python agent_extensions/continuity/adapters/capsule.py render --format claude

python agent_extensions/continuity/adapters/capsule.py status --json
python agent_extensions/continuity/adapters/capsule.py clear
```

`python -m agent_extensions.continuity ...` works too when the repo is on
`PYTHONPATH`.

## Adapters

| Harness | File | Wiring |
|---|---|---|
| pi | `adapters/pi-extension.ts` | injects once on `before_agent_start` after `session_start`; captures on `session_before_compact`, `session_compact`, `session_shutdown` |
| Claude Code | `adapters/claude_hook.py` + `claude-hooks.snippet.json` | `SessionStart` renders `additionalContext`; `PreCompact` captures auto state |
| any | `adapters/capsule.py` | call it from a startup script or wrapper |

Adapters fail closed to a no-op: a continuity error never breaks a session.

## Provider neutrality

- Store, schema, and CLI are plain Python + JSON, no SDK.
- Harness detection is env-based and returns `""` when unknown.
- The only provider-aware files are the thin adapters, one per harness.
