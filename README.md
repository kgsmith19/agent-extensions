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
