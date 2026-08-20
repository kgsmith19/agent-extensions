# bootstrap/sync-external-codex.test.ps1
#!/usr/bin/env pwsh
$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $PSScriptRoot
. (Join-Path $PSScriptRoot "sync.ps1") -Import
. (Join-Path $RepoRoot "bootstrap\tests\fixtures\marketplace-fixture.ps1")

$scratch = Join-Path ([System.IO.Path]::GetTempPath()) "agent-extensions-extcodex-$(Get-Random)"
$fixtureRepo = Join-Path $scratch "fixture-marketplace"
$declareRoot = Join-Path $scratch "declare-root"
$vendorCache = Join-Path $scratch "vendor-cache"
$codexSkillsDir = Join-Path $scratch "agents-skills"
$codexConfigPath = Join-Path $scratch "codex-config.toml"
New-Item -ItemType Directory -Path (Join-Path $declareRoot "bootstrap") -Force | Out-Null
New-Item -ItemType Directory -Path $codexSkillsDir -Force | Out-Null

$sha = New-FixtureMarketplace -DestDir $fixtureRepo
$manifest = @{
    marketplaces = @(
        @{ name = "fixture-mp"; repo = $fixtureRepo; pinnedCommit = $sha;
           plugins = @("alpha-skills", "beta-mcp-stdio", "gamma-mcp-http", "delta-malformed") }
    )
} | ConvertTo-Json -Depth 10
Set-Content -Path (Join-Path $declareRoot "bootstrap\external-marketplaces.json") -Value $manifest
Sync-VendorCache -RepoRoot $declareRoot -VendorCacheDir $vendorCache | Out-Null

$failures = @()

$reported = Sync-ExternalCodexContent -RepoRoot $declareRoot -VendorCacheDir $vendorCache -CodexSkillsDir $codexSkillsDir -CodexConfigPath $codexConfigPath

# --- Partial-failure isolation: delta-malformed fails, others still succeed ---
if ($reported.Count -eq 0) {
    $failures += "Expected a reported failure for delta-malformed, got none"
}

$greetLink = Join-Path $codexSkillsDir "greet"
$greetItem = Get-Item $greetLink -ErrorAction SilentlyContinue
if (-not $greetItem -or -not $greetItem.LinkType) {
    $failures += "alpha-skills: 'greet' skill was not linked (or is not a live link) despite delta-malformed's failure"
}

$configContent = if (Test-Path $codexConfigPath) { Get-Content $codexConfigPath -Raw } else { "" }
if ($configContent -notmatch [regex]::Escape('[mcp_servers.fixture-stdio]')) {
    $failures += "beta-mcp-stdio's server was not merged into config.toml"
}
if ($configContent -notmatch [regex]::Escape('[mcp_servers.fixture-http]')) {
    $failures += "gamma-mcp-http's server was not merged into config.toml"
}
if ($configContent -match 'fixture-bad') {
    $failures += "delta-malformed's unrecognized server should not appear in config.toml at all"
}

Remove-Item -Recurse -Force $scratch

if ($failures.Count -gt 0) {
    Write-Output "FAIL: Sync-ExternalCodexContent"
    $failures | ForEach-Object { Write-Output "  - $_" }
    exit 1
}

Write-Output "PASS: Sync-ExternalCodexContent links skills, merges MCP, isolates one plugin's failure"
exit 0
