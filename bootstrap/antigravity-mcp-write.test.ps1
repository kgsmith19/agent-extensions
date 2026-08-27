#!/usr/bin/env pwsh
$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "sync.ps1") -Import

$scratch = Join-Path ([System.IO.Path]::GetTempPath()) "agent-extensions-agmcpwrite-$(Get-Random)"
$pluginDir = Join-Path $scratch "fixture-http"

$failures = @()

$json = '{"mcpServers":{"fixture-http":{"url":"https://fixture.example.com/mcp"}}}'
Write-AntigravityMcpConfig -PluginStagedDir $pluginDir -JsonContent $json
$written = Get-Content (Join-Path $pluginDir "mcp_config.json") -Raw
if (($written | ConvertFrom-Json).mcpServers.'fixture-http'.url -ne "https://fixture.example.com/mcp") {
    $failures += "Written mcp_config.json does not round-trip the input JSON"
}

# --- Idempotent overwrite ---
Write-AntigravityMcpConfig -PluginStagedDir $pluginDir -JsonContent $json
$writtenAgain = Get-Content (Join-Path $pluginDir "mcp_config.json") -Raw
if ($writtenAgain -ne $written) {
    $failures += "Re-writing identical input changed the file (not idempotent)"
}

# --- Empty content is a no-op, not an empty file ---
$emptyDir = Join-Path $scratch "no-mcp-plugin"
Write-AntigravityMcpConfig -PluginStagedDir $emptyDir -JsonContent ""
if (Test-Path (Join-Path $emptyDir "mcp_config.json")) {
    $failures += "Empty JsonContent should not create mcp_config.json"
}

Remove-Item -Recurse -Force $scratch

if ($failures.Count -gt 0) {
    Write-Output "FAIL: Write-AntigravityMcpConfig"
    $failures | ForEach-Object { Write-Output "  - $_" }
    exit 1
}

Write-Output "PASS: Write-AntigravityMcpConfig writes, is idempotent, no-ops on empty input"
exit 0
