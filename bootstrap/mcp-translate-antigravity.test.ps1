# bootstrap/mcp-translate-antigravity.test.ps1
#!/usr/bin/env pwsh
$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "sync.ps1") -Import

$failures = @()

$stdio = @'
{ "fixture-stdio": { "command": "node", "args": ["server.js", "--port", "0"], "env": { "FIXTURE_MODE": "stdio" } } }
'@ | ConvertFrom-Json
$stdioConfig = ConvertTo-AntigravityMcpConfig -McpServers $stdio
$parsedStdio = $stdioConfig | ConvertFrom-Json
if ($parsedStdio.mcpServers.'fixture-stdio'.command -ne "node") {
    $failures += "stdio: command not preserved in Antigravity config"
}
if (($parsedStdio.mcpServers.'fixture-stdio'.args -join ",") -ne "server.js,--port,0") {
    $failures += "stdio: args not preserved in Antigravity config"
}
if ($parsedStdio.mcpServers.'fixture-stdio'.env.FIXTURE_MODE -ne "stdio") {
    $failures += "stdio: env not preserved in Antigravity config"
}

$http = @'
{ "fixture-http": { "url": "https://fixture.example.com/mcp", "headers": { "Authorization": "Bearer FIXTURE_TOKEN" } } }
'@ | ConvertFrom-Json
$httpConfig = ConvertTo-AntigravityMcpConfig -McpServers $http
$parsedHttp = $httpConfig | ConvertFrom-Json
if ($parsedHttp.mcpServers.'fixture-http'.url -ne "https://fixture.example.com/mcp") {
    $failures += "http: url not preserved in Antigravity config"
}
if ($parsedHttp.mcpServers.'fixture-http'.headers.Authorization -ne "Bearer FIXTURE_TOKEN") {
    $failures += "http: headers not preserved in Antigravity config"
}

$empty = [PSCustomObject]@{}
$emptyParsed = (ConvertTo-AntigravityMcpConfig -McpServers $empty) | ConvertFrom-Json
# Note: wrapped in @(...) before .Count — on PSCustomObject.PSObject.Properties with zero
# members, .Count returns $null (not 0) on this PowerShell version, which would make
# "-ne 0" spuriously true. Forcing array context makes .Count a reliable integer.
if (@($emptyParsed.mcpServers.PSObject.Properties).Count -ne 0) {
    $failures += "empty: expected mcpServers to be an empty object"
}

$bad = @'
{ "fixture-bad": { "transportKind": "carrier-pigeon" } }
'@ | ConvertFrom-Json
$threw = $false
try { ConvertTo-AntigravityMcpConfig -McpServers $bad | Out-Null } catch { $threw = $true }
if (-not $threw) {
    $failures += "malformed: expected ConvertTo-AntigravityMcpConfig to throw, it did not"
}

# --- malformed: both command and url ---
$bothTransports = @'
{ "fixture-both": { "command": "node", "url": "https://example.com" } }
'@ | ConvertFrom-Json
$threwBoth = $false
try { ConvertTo-AntigravityMcpConfig -McpServers $bothTransports | Out-Null } catch { $threwBoth = $true }
if (-not $threwBoth) {
    $failures += "malformed (both): expected ConvertTo-AntigravityMcpConfig to throw, it did not"
}

# --- malformed: unrecognized field on an otherwise-valid stdio server ---
$extraField = @'
{ "fixture-extra": { "command": "node", "cwd": "/tmp" } }
'@ | ConvertFrom-Json
$threwExtra = $false
try { ConvertTo-AntigravityMcpConfig -McpServers $extraField | Out-Null } catch { $threwExtra = $true }
if (-not $threwExtra) {
    $failures += "malformed (extra field): expected ConvertTo-AntigravityMcpConfig to throw, it did not"
}

if ($failures.Count -gt 0) {
    Write-Output "FAIL: ConvertTo-AntigravityMcpConfig"
    $failures | ForEach-Object { Write-Output "  - $_" }
    exit 1
}

Write-Output "PASS: ConvertTo-AntigravityMcpConfig handles stdio, http, empty, and malformed input"
exit 0
