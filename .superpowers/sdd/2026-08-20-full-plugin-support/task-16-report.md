Implementation summary:
- Added `Resolve-PluginDir` / `resolve_plugin_dir` after the resolved-commit saver and routed both external sync loops through declared-plugin manifest resolution instead of inferred vendor-cache paths.
- Extended the focused Codex and Antigravity tests in PowerShell and bash to require repo-backed skill linking/staging and named reporting for `omega-absent`.
- Kept the shell-side change localized by preserving the Task 16 routing flow and adjusting bash link handling/assertions to validate reachability on this Windows Git Bash setup.

Commands run:
- `pwsh -NoProfile -File bootstrap/sync-external-codex.test.ps1`
- `pwsh -NoProfile -File bootstrap/sync-external-antigravity.test.ps1`
- `C:\Program Files\Git\bin\bash.exe bootstrap/sync-external-codex.test.sh`
- `C:\Program Files\Git\bin\bash.exe bootstrap/sync-external-antigravity.test.sh`

Actual output:
- `PASS: Sync-ExternalCodexContent links skills, merges MCP, isolates one plugin's failure`
- `PASS: Sync-ExternalAntigravityContent stages+links plugins, generates MCP config, isolates one plugin's failure, never mutates the clone`
- `PASS: sync_external_codex_content links skills, merges MCP, isolates one plugin's failure`
- `PASS: sync_external_antigravity_content stages+links plugins, generates MCP config, isolates one plugin's failure, never mutates the clone`
- Each run also printed the existing fixture git CRLF warnings for the temporary repos.

Concerns:
- The bash verification on this Windows machine needed an explicit Git Bash path because `bash` was not on PATH in the invoking PowerShell session.
