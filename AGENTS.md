# agent-extensions

Kyle's personal cross-provider Claude Skills marketplace. Two plugins:

- `anthropic-product-skills` — Claude.ai product-surface skills (Canvas,
  Artifacts). Claude-only by nature; inert on Codex and Antigravity.
- `general-skills` — skills useful on any provider, starting with
  `skill-creator`.

## Adding a new skill

1. Decide which plugin it belongs to (or create a new themed plugin under
   `plugins/`, following the existing structure: `.claude-plugin/plugin.json`,
   `plugin.json`, `.codex-plugin/plugin.json`, `skills/`).
2. Add the skill folder under that plugin's `skills/`.
3. If vendored from elsewhere, add a line to that plugin's `skills/VENDORED-FROM`.
4. If it's a new plugin, add it to `.claude-plugin/marketplace.json`.
5. Run `bootstrap/sync.ps1` (or `sync.sh`) to re-link it into every provider.

See `docs/superpowers/specs/2026-08-20-agent-extensions-design.md` for the
full design rationale.
