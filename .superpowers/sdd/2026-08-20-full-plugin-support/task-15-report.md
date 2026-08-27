Implementation summary:
- Added `Save-ResolvedCommit` in `bootstrap/sync.ps1` and `save_resolved_commit` in `bootstrap/sync.sh` to persist first-use `resolvedCommit` pins for declared unpinned plugins, while preserving unrelated marketplace and plugin entries.
- Added focused PowerShell and Bash tests that prove conversion from string to object entry, replacement-on-update without duplication, preservation of unrelated entries, and explicit errors for unknown marketplace or plugin names.

Commands run:
- `pwsh -NoProfile -File bootstrap/resolved-commit.test.ps1`
- `& 'C:\Program Files\Git\bin\bash.exe' bootstrap/resolved-commit.test.sh`

Actual output:
```text
PASS: Save-ResolvedCommit pins one plugin, preserves the rest, replaces on update, errors on unknown names
PASS: save_resolved_commit pins one plugin, preserves the rest, replaces on update, errors on unknown names
```

Concerns:
- `bash` was not on the PowerShell PATH in this environment, so the Bash verification used `C:\Program Files\Git\bin\bash.exe` directly.
