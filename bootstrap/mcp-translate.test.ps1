# bootstrap/mcp-translate.test.ps1
#!/usr/bin/env pwsh
$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "sync.ps1") -Import

$failures = @()

# --- stdio server ---
$stdio = @'
{ "fixture-stdio": { "command": "node", "args": ["server.js", "--port", "0"], "env": { "FIXTURE_MODE": "stdio" } } }
'@ | ConvertFrom-Json
$stdioToml = ConvertTo-CodexMcpToml -McpServers $stdio
if ($stdioToml -notmatch '\[mcp_servers\.fixture-stdio\]') {
    $failures += "stdio: missing table header"
}
if ($stdioToml -notmatch 'command = "node"') {
    $failures += "stdio: missing/incorrect command"
}
if ($stdioToml -notmatch 'args = \["server\.js", "--port", "0"\]') {
    $failures += "stdio: missing/incorrect args"
}
if ($stdioToml -notmatch 'env = \{ FIXTURE_MODE = "stdio" \}') {
    $failures += "stdio: missing/incorrect env"
}

# --- http server ---
$http = @'
{ "fixture-http": { "url": "https://fixture.example.com/mcp", "headers": { "Authorization": "Bearer FIXTURE_TOKEN" } } }
'@ | ConvertFrom-Json
$httpToml = ConvertTo-CodexMcpToml -McpServers $http
if ($httpToml -notmatch '\[mcp_servers\.fixture-http\]') {
    $failures += "http: missing table header"
}
if ($httpToml -notmatch 'url = "https://fixture\.example\.com/mcp"') {
    $failures += "http: missing/incorrect url"
}
if ($httpToml -notmatch 'http_headers = \{ Authorization = "Bearer FIXTURE_TOKEN" \}') {
    $failures += "http: missing/incorrect headers"
}

# --- zero servers ---
$empty = [PSCustomObject]@{}
if ((ConvertTo-CodexMcpToml -McpServers $empty) -ne "") {
    $failures += "empty: expected empty string for zero MCP servers"
}

# --- malformed: neither command nor url ---
$bad = @'
{ "fixture-bad": { "transportKind": "carrier-pigeon" } }
'@ | ConvertFrom-Json
$threw = $false
try { ConvertTo-CodexMcpToml -McpServers $bad | Out-Null } catch { $threw = $true }
if (-not $threw) {
    $failures += "malformed: expected ConvertTo-CodexMcpToml to throw, it did not"
}

# --- malformed: both command and url ---
$bothTransports = @'
{ "fixture-both": { "command": "node", "url": "https://example.com" } }
'@ | ConvertFrom-Json
$threwBoth = $false
try { ConvertTo-CodexMcpToml -McpServers $bothTransports | Out-Null } catch { $threwBoth = $true }
if (-not $threwBoth) {
    $failures += "malformed (both): expected ConvertTo-CodexMcpToml to throw, it did not"
}

# --- malformed: unrecognized field on an otherwise-valid stdio server ---
$extraField = @'
{ "fixture-extra": { "command": "node", "cwd": "/tmp" } }
'@ | ConvertFrom-Json
$threwExtra = $false
try { ConvertTo-CodexMcpToml -McpServers $extraField | Out-Null } catch { $threwExtra = $true }
if (-not $threwExtra) {
    $failures += "malformed (extra field): expected ConvertTo-CodexMcpToml to throw, it did not"
}

# --- backslash escaping regression: command/args containing literal backslashes ---
$pathServer = @'
{ "fixture-path": { "command": "C:\\Tools\\node.exe", "args": ["C:\\scripts\\server.js"] } }
'@ | ConvertFrom-Json
$pathToml = ConvertTo-CodexMcpToml -McpServers $pathServer
if ($pathToml -notmatch 'command = "C:\\\\Tools\\\\node\.exe"') {
    $failures += "backslash escaping: command with backslashes not doubled correctly, got: $pathToml"
}
if ($pathToml -notmatch 'args = \["C:\\\\scripts\\\\server\.js"\]') {
    $failures += "backslash escaping: args with backslashes not doubled correctly, got: $pathToml"
}

if ($failures.Count -gt 0) {
    Write-Output "FAIL: ConvertTo-CodexMcpToml"
    $failures | ForEach-Object { Write-Output "  - $_" }
    exit 1
}

Write-Output "PASS: ConvertTo-CodexMcpToml handles stdio, http, empty, and malformed input"
exit 0
