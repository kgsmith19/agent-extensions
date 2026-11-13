<!-- managed-by: agent-extensions -->

# Kilo shim

Canonical global rules: `~\AGENTS.md`
Canonical agent/secrets config: `~/.config/agents\`

Use this file only for Kilo-specific integration. Do not duplicate global rules here.

- Runtime: `kilo.jsonc` in this directory (model/provider/permissions).
- Bootstrap identity only: `.env` (`INFISICAL_CLIENT_ID`, `INFISICAL_CLIENT_SECRET`, `INFISICAL_PROJECT_ID`).
- Repo access: the `gh` CLI, authenticated with the scoped CLI token from the secret provider (GitHub Apps are retired).

Repo-specific Kilo notes belong in that repo, not here.
