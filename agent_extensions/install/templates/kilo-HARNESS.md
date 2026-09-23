<!-- managed-by: agent-extensions -->

# Kilo shim

Canonical global rules: `~\AGENTS.md`
Canonical agent/secrets config: `~/.config/agents\`

Use this file only for Kilo-specific integration. Do not duplicate global rules here.

- Runtime: `kilo.jsonc` in this directory (model/provider/permissions).
- Bootstrap identity only: `.env` (`INFISICAL_CLIENT_ID`, `INFISICAL_CLIENT_SECRET`, `INFISICAL_PROJECT_ID`).
- Mint repo tokens: `pwsh -NoProfile -File "$env:USERPROFILE\.config\agents\fetch-agent-secrets.ps1" -Agent dev-agent -Output gh`

Repo-specific Kilo notes belong in that repo, not here.
