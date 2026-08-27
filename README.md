# agent-extensions

A private, cross-provider Claude Skills marketplace for Claude Code, Codex,
and Antigravity. See [AGENTS.md](./AGENTS.md) for what's here and how to add
to it, and [the design spec](./docs/superpowers/specs/2026-08-20-agent-extensions-design.md)
for why it's built this way.

## Install

```bash
pwsh bootstrap/sync.ps1   # Windows
./bootstrap/sync.sh       # Unix / cloud containers
```

This also syncs Skills and MCP servers from the external marketplaces
declared in `bootstrap/external-marketplaces.json` — see
[AGENTS.md](./AGENTS.md#external-marketplaces).
