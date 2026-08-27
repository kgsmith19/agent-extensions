2026-08-27

Implementation summary:
- Added `Sync-PluginRepo` to `bootstrap/sync.ps1` and `sync_plugin_repo` to `bootstrap/sync.sh`.
- Added focused PowerShell and bash regression tests for external plugin repo clone behavior, covering sha precedence, subpath handling, unpinned HEAD refresh, idempotent reruns, and reported errors for unreachable commits and missing subpaths.
- Kept this task scoped to the helper and its direct tests; the broader external-plugin sync loop wiring remains for the later slice that consumes `resolvedCommit`.

Commands run:
- `pwsh -NoProfile -File bootstrap/plugin-repo.test.ps1`
- `& 'C:\Program Files\Git\bin\bash.exe' bootstrap/plugin-repo.test.sh`
- `pwsh -NoProfile -File bootstrap/plugin-repo.test.ps1; if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }; & 'C:\Program Files\Git\bin\bash.exe' bootstrap/plugin-repo.test.sh; exit $LASTEXITCODE`

Actual output:
```text
warning: in the working copy of 'nested/eta/skills/eta-greet/SKILL.md', LF will be replaced by CRLF the next time Git touches it
warning: in the working copy of 'skills/remote-greet/SKILL.md', LF will be replaced by CRLF the next time Git touches it
warning: in the working copy of 'external_plugins/gamma-mcp-http/.mcp.json', LF will be replaced by CRLF the next time Git touches it
warning: in the working copy of 'plugins/alpha-skills/skills/greet/SKILL.md', LF will be replaced by CRLF the next time Git touches it
warning: in the working copy of 'plugins/beta-mcp-stdio/.mcp.json', LF will be replaced by CRLF the next time Git touches it
warning: in the working copy of 'plugins/delta-malformed/.mcp.json', LF will be replaced by CRLF the next time Git touches it
PASS: Sync-PluginRepo handles sha, ref, subpath, unpinned HEAD, idempotency, and reports unreachable commits
warning: in the working copy of 'nested/eta/skills/eta-greet/SKILL.md', LF will be replaced by CRLF the next time Git touches it
warning: in the working copy of 'skills/remote-greet/SKILL.md', LF will be replaced by CRLF the next time Git touches it
warning: in the working copy of 'external_plugins/gamma-mcp-http/.mcp.json', LF will be replaced by CRLF the next time Git touches it
warning: in the working copy of 'plugins/alpha-skills/skills/greet/SKILL.md', LF will be replaced by CRLF the next time Git touches it
warning: in the working copy of 'plugins/beta-mcp-stdio/.mcp.json', LF will be replaced by CRLF the next time Git touches it
warning: in the working copy of 'plugins/delta-malformed/.mcp.json', LF will be replaced by CRLF the next time Git touches it
PASS: sync_plugin_repo handles sha, ref, subpath, unpinned HEAD, idempotency, and reports unreachable commits
```

Concerns:
- The fixture setup emits Git line-ending warnings on this Windows checkout; the task verification still passed.
- Task 14 adds the clone helper and tests only. The existing external sync loops still need the later slice that wires in `Get-DeclaredPlugins` and `resolvedCommit`.
