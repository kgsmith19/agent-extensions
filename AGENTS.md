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

## External marketplaces

`bootstrap/external-marketplaces.json` declares Claude Code marketplaces this
repo depends on (currently `claude-plugins-official`, `superpowers-marketplace`)
and which of their plugins to sync. `sync.ps1`/`sync.sh` clone each to a pinned
commit under `.vendor-cache/` (gitignored, not committed), then port Skills and
MCP servers from those plugins to Codex and Antigravity the same way this
repo's own plugins are — Claude Code registers the marketplaces natively and
tracks their live HEAD, same as it already does for any marketplace you add
by hand.

To add a plugin: add its name to the right marketplace's `plugins` array in
`external-marketplaces.json`, then re-run sync. To bump a marketplace's pinned
commit: update `pinnedCommit`, then re-run sync — this is a manual, deliberate
action, same as re-syncing a vendored skill.

Agents, hooks, and commands from these plugins are not ported — see
`docs/superpowers/specs/2026-08-20-full-plugin-support-design.md` for why.

## Verifying a sync

`bootstrap/sync.ps1` links content; `bootstrap/verify-external.ps1` proves
it landed. Run both after any change to the declared roster:

    pwsh bootstrap/sync.ps1
    pwsh bootstrap/verify-external.ps1

Verify exits non-zero and names every plugin whose skills or links are
missing. It also fails if the whole roster resolves to zero skills, which is
what a silent resolution bug looks like.

Plugins whose marketplace declares no commit are pinned on first sync: the
resolved SHA is written back into `bootstrap/external-marketplaces.json` as
`resolvedCommit`. Commit that change — it is the lockfile.

See `docs/superpowers/specs/2026-08-20-agent-extensions-design.md` for the
full design rationale.
