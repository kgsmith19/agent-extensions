# bootstrap/codex-mcp-merge.test.ps1
#!/usr/bin/env pwsh
$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "sync.ps1") -Import

$scratch = Join-Path ([System.IO.Path]::GetTempPath()) "agent-extensions-codexmerge-$(Get-Random)"
New-Item -ItemType Directory -Path $scratch -Force | Out-Null
$configPath = Join-Path $scratch "config.toml"

$unrelated = "[some_other_section]`nfoo = `"bar`"`n"
Set-Content -Path $configPath -Value $unrelated -NoNewline

$failures = @()

Merge-CodexMcpConfig -ConfigPath $configPath -TomlByPlugin @{
    "beta-mcp-stdio" = "[mcp_servers.fixture-stdio]`ncommand = `"node`""
    "gamma-mcp-http" = "[mcp_servers.fixture-http]`nurl = `"https://fixture.example.com/mcp`""
}
$afterFirst = Get-Content $configPath -Raw

if ($afterFirst -notmatch [regex]::Escape('[some_other_section]')) {
    $failures += "Merge dropped pre-existing unrelated content"
}
if ($afterFirst -notmatch [regex]::Escape('[mcp_servers.fixture-stdio]')) {
    $failures += "Merge did not add the stdio server table"
}
if ($afterFirst -notmatch [regex]::Escape('[mcp_servers.fixture-http]')) {
    $failures += "Merge did not add the http server table"
}

# --- Idempotency: identical re-merge produces identical output ---
Merge-CodexMcpConfig -ConfigPath $configPath -TomlByPlugin @{
    "beta-mcp-stdio" = "[mcp_servers.fixture-stdio]`ncommand = `"node`""
    "gamma-mcp-http" = "[mcp_servers.fixture-http]`nurl = `"https://fixture.example.com/mcp`""
}
$afterSecond = Get-Content $configPath -Raw
if ($afterSecond -ne $afterFirst) {
    $failures += "Re-merging identical input changed the file (not idempotent)"
}

# --- Changed input: old managed content replaced, unrelated content preserved ---
Merge-CodexMcpConfig -ConfigPath $configPath -TomlByPlugin @{
    "beta-mcp-stdio" = "[mcp_servers.fixture-stdio]`ncommand = `"node`""
}
$afterChange = Get-Content $configPath -Raw
if ($afterChange -match [regex]::Escape('[mcp_servers.fixture-http]')) {
    $failures += "Old managed content (fixture-http) was not removed after a changed merge"
}
if ($afterChange -notmatch [regex]::Escape('[some_other_section]')) {
    $failures += "Unrelated content was lost after a changed merge"
}

Remove-Item -Recurse -Force $scratch

if ($failures.Count -gt 0) {
    Write-Output "FAIL: Merge-CodexMcpConfig"
    $failures | ForEach-Object { Write-Output "  - $_" }
    exit 1
}

Write-Output "PASS: Merge-CodexMcpConfig preserves unrelated content, idempotent, replaces on change"
exit 0
