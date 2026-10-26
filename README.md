# agent-extensions

Kyle's personal cross-provider agent capability source. Two layers:

- **One-line setup (issue #53)** — `bootstrap.sh` installs the machine layer
  (baseline rules, harness shims, global gitignore for local overlays,
  continuity hooks, the `ae` launcher) plus the capability bundle, idempotently.
- **Capability supply** — skills/plugins linked into every detected harness.

See [AGENTS.md](./AGENTS.md) for what's here and how to add to it, and
[the design spec](./docs/superpowers/specs/2026-08-20-agent-extensions-design.md)
for why it's built this way.

## Install

```bash
curl -fsSL https://raw.githubusercontent.com/kgsmith19/agent-extensions/main/bootstrap.sh | bash
```

After the first bootstrap, the everyday command is `ae`:

    ae update        # grab the latest checkout + re-converge the machine
    ae init [repo]   # forceful repo-mode injection (gitignored): AGENTS.local.md overlay, harness bindings, compliance guard
    ae status        # read-only per-stage state report
    ae bootstrap     # re-run the full machine install (idempotent)

Repo-mode writes only gitignored paths (AGENTS.local.md,
.claude/settings.local.json, .pi/settings.json when absent) — your repo's
committed files are never touched. Whole-file templates are marker-guarded:
files without the managed-by marker are reported and skipped, never clobbered.
Customize by editing templates/ or dropping fragments in AGENTS.local.d/.

This also syncs Skills and MCP servers from the external marketplaces
declared in `bootstrap/external-marketplaces.json` — see
[AGENTS.md](./AGENTS.md#external-marketplaces).

## Execution queue at every session start

The owner's execution queue (agent-engineering-standard `tools/work_queue.py`,
queue v2) says what to work on next. Every session should start by printing
where it is in the queue: the **active item**, the **next item**, and the
**handoff** (how to work it). Two equivalent entry points do this, with no model
or provider dependency:

    bash bootstrap/session-start.sh [--env-file FILE]   # POSIX sh
    pwsh -File bootstrap/session-start.ps1 [-EnvFile FILE]   # PowerShell
    ae queue brief                                       # just the queue part

Each script provisions the capability layer only when it is missing, puts
`~/bin` on PATH once, prints the queue brief, and always exits 0. A failure is
reported as `degraded`, never hidden. `ae queue <args>` runs any std queue
command (`next`, `show`, `history Q10`, `handoff show`, `move …`) with its exit
code preserved: 3 means the queue is empty, 5 means std could not be reached.

| Harness | How the brief runs at session start |
| --- | --- |
| Claude Code | `.claude/settings.json` `SessionStart` hook → `.claude/hooks/session-start.sh` → `bootstrap/session-start.sh` (already set up in the owner's three repos). |
| Codex CLI | A `SessionStart` hook (Codex's hook vocabulary, see `bootstrap/hook-translate.test.sh`) whose command is `bash ~/.agent-extensions/bootstrap/session-start.sh`. |
| Gemini CLI | A `SessionStart` hook where your version supports hooks; otherwise make `ae queue brief` the session's first command. |
| Kilo | `pwsh -NoProfile -File ~/.agent-extensions/bootstrap/session-start.ps1` (or the sh script) as the first command of the session. |
| Plain shell / any other harness | Run either script, or `ae queue brief`, before starting work. |

Claims are guarded in the harness too: `ae bootstrap` registers
`agent_extensions/queue_hook.py` as a Claude Code `PreToolUse` hook (matcher
`mcp__github__create_branch|Bash`). Creating an `issue/<n>-<slug>` branch,
the claim protocol's atomic step, is blocked (exit 2, with the reason) unless
`work_queue.py guard --claim` accepts that Issue. It is also blocked when the
queue cannot be read. Every other tool call passes without a network call.

Where std's CLI comes from: a local agent-engineering-standard checkout when
`AES_STANDARD_DIR` points at it, otherwise every queue module is fetched from
`AE_QUEUE_BASE_URL` (default: std `main` on raw.githubusercontent.com) into a
temp dir, once per run. Queue writes record the harness and provider from
`AES_QUEUE_HARNESS` / `AES_QUEUE_PROVIDER` when set; set them in harnesses std
cannot detect, such as Kilo.
