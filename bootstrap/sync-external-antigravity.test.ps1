# bootstrap/sync-external-antigravity.test.ps1
#!/usr/bin/env pwsh
$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $PSScriptRoot
. (Join-Path $PSScriptRoot "sync.ps1") -Import
. (Join-Path $RepoRoot "bootstrap\tests\fixtures\marketplace-fixture.ps1")

$scratch = Join-Path ([System.IO.Path]::GetTempPath()) "agent-extensions-extantigravity-$(Get-Random)"
$fixtureRepo = Join-Path $scratch "fixture-marketplace"
$declareRoot = Join-Path $scratch "declare-root"
$vendorCache = Join-Path $scratch "vendor-cache"
$stagedDir = Join-Path $scratch "staged"
$antigravityPluginsDir = Join-Path $scratch "gemini-plugins"
New-Item -ItemType Directory -Path (Join-Path $declareRoot "bootstrap") -Force | Out-Null
New-Item -ItemType Directory -Path $antigravityPluginsDir -Force | Out-Null

$sha = New-FixtureMarketplace -DestDir $fixtureRepo
$manifest = @{
    marketplaces = @(
        @{ name = "fixture-mp"; repo = $fixtureRepo; pinnedCommit = $sha;
           plugins = @("alpha-skills", "beta-mcp-stdio", "gamma-mcp-http", "epsilon-invalid-json", "delta-malformed") }
    )
} | ConvertTo-Json -Depth 10
Set-Content -Path (Join-Path $declareRoot "bootstrap\external-marketplaces.json") -Value $manifest
Sync-VendorCache -RepoRoot $declareRoot -VendorCacheDir $vendorCache | Out-Null

$failures = @()
$reported = Sync-ExternalAntigravityContent -RepoRoot $declareRoot -VendorCacheDir $vendorCache -StagedDir $stagedDir -AntigravityPluginsDir $antigravityPluginsDir

if ($reported.Count -eq 0) {
    $failures += "Expected a reported failure for delta-malformed, got none"
}

# --- Regression: syntactically-invalid .mcp.json (JSON parse error, not just a
# semantically-wrong shape) must be caught and reported per-plugin, not throw
# an uncaught terminating error that aborts the whole function — and
# processing must continue past it to later plugins (delta-malformed, checked
# below, comes after epsilon-invalid-json in the declared plugin order).
if (-not ($reported | Where-Object { $_ -match 'epsilon-invalid-json' })) {
    $failures += "Expected a reported failure mentioning epsilon-invalid-json (invalid JSON)"
}

# --- alpha-skills: linked, contains its skill via the staged dir ---
$alphaLink = Join-Path $antigravityPluginsDir "alpha-skills"
$alphaItem = Get-Item $alphaLink -ErrorAction SilentlyContinue
if (-not $alphaItem -or -not $alphaItem.LinkType) {
    $failures += "alpha-skills was not linked into the Antigravity plugins dir"
}
if (-not (Test-Path (Join-Path $alphaLink "skills\greet\SKILL.md"))) {
    $failures += "alpha-skills' skill is not reachable through the Antigravity link"
}

# --- beta-mcp-stdio: linked, has generated mcp_config.json ---
$betaLink = Join-Path $antigravityPluginsDir "beta-mcp-stdio"
$betaConfig = Join-Path $betaLink "mcp_config.json"
if (-not (Test-Path $betaConfig)) {
    $failures += "beta-mcp-stdio's mcp_config.json was not generated/linked"
} else {
    $parsed = Get-Content $betaConfig -Raw | ConvertFrom-Json
    if ($parsed.mcpServers.'fixture-stdio'.command -ne "node") {
        $failures += "beta-mcp-stdio's mcp_config.json has wrong content"
    }
}

# --- delta-malformed: must not silently appear as if it succeeded ---
$deltaConfig = Join-Path $antigravityPluginsDir "delta-malformed\mcp_config.json"
if (Test-Path $deltaConfig) {
    $failures += "delta-malformed should not have produced an mcp_config.json"
}

# --- epsilon-invalid-json: must not silently appear as if it succeeded ---
$epsilonConfig = Join-Path $antigravityPluginsDir "epsilon-invalid-json\mcp_config.json"
if (Test-Path $epsilonConfig) {
    $failures += "epsilon-invalid-json should not have produced an mcp_config.json"
}

# --- Never mutate the pinned vendor-cache clone ---
$vendorCloneMcpConfig = Join-Path $vendorCache "fixture-mp\beta-mcp-stdio\mcp_config.json"
if (Test-Path $vendorCloneMcpConfig) {
    $failures += "Sync wrote a generated file into the pinned vendor-cache clone — it must only write to the staged dir"
}

Remove-Item -Recurse -Force $scratch

if ($failures.Count -gt 0) {
    Write-Output "FAIL: Sync-ExternalAntigravityContent"
    $failures | ForEach-Object { Write-Output "  - $_" }
    exit 1
}

Write-Output "PASS: Sync-ExternalAntigravityContent stages+links plugins, generates MCP config, isolates one plugin's failure, never mutates the clone"
exit 0
