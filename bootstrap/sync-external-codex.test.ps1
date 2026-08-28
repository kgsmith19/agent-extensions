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
$codexAgentsDir = Join-Path $scratch "codex-agents"
$codexHooksConfigPath = Join-Path $scratch "codex-hooks.json"
New-Item -ItemType Directory -Path (Join-Path $declareRoot "bootstrap") -Force | Out-Null
New-Item -ItemType Directory -Path $codexSkillsDir -Force | Out-Null

$repoDir = Join-Path $scratch "plugin-repo"
$repoSha = New-FixturePluginRepo -DestDir $repoDir
$sha = New-FixtureMarketplace -DestDir $fixtureRepo -PluginRepoDir $repoDir -PluginRepoSha $repoSha
$manifest = @{
    marketplaces = @(
        @{ name = "fixture-mp"; repo = $fixtureRepo; pinnedCommit = $sha;
           plugins = @("alpha-skills", "beta-mcp-stdio", "gamma-mcp-http", "epsilon-invalid-json", "delta-malformed", "zeta-repo-pinned", "eta-repo-subpath", "omega-absent") }
    )
} | ConvertTo-Json -Depth 10
Set-Content -Path (Join-Path $declareRoot "bootstrap\external-marketplaces.json") -Value $manifest
Sync-VendorCache -RepoRoot $declareRoot -VendorCacheDir $vendorCache | Out-Null

$failures = @()

$reported = Sync-ExternalCodexContent -RepoRoot $declareRoot -VendorCacheDir $vendorCache -CodexSkillsDir $codexSkillsDir -CodexConfigPath $codexConfigPath -CodexAgentsDir $codexAgentsDir -CodexHooksConfigPath $codexHooksConfigPath

# --- Partial-failure isolation: delta-malformed fails, others still succeed ---
if ($reported.Count -eq 0) {
    $failures += "Expected a reported failure for delta-malformed, got none"
}

# --- Regression: syntactically-invalid .mcp.json (JSON parse error, not just a
# semantically-wrong shape) must be caught and reported per-plugin, not throw
# an uncaught terminating error that aborts the whole function — and
# processing must continue past it to later plugins (delta-malformed, checked
# above, comes after epsilon-invalid-json in the declared plugin order).
if (-not ($reported | Where-Object { $_ -match 'epsilon-invalid-json' })) {
    $failures += "Expected a reported failure mentioning epsilon-invalid-json (invalid JSON)"
}

$greetLink = Join-Path $codexSkillsDir "greet"
$greetItem = Get-Item $greetLink -ErrorAction SilentlyContinue
if (-not $greetItem -or -not $greetItem.LinkType) {
    $failures += "alpha-skills: 'greet' skill was not linked (or is not a live link) despite delta-malformed's failure"
}
if (-not (Test-Path (Join-Path $codexSkillsDir "remote-greet"))) {
    $failures += "zeta-repo-pinned's 'remote-greet' skill was not linked from its external repo"
}
if (-not (Test-Path (Join-Path $codexSkillsDir "eta-greet"))) {
    $failures += "eta-repo-subpath's 'eta-greet' skill was not linked from its repo subdirectory"
}

$omegaReported = @($reported) | Where-Object { $_ -match "omega-absent" }
if (-not $omegaReported) {
    $failures += "omega-absent is declared but not in the manifest; it must be reported as a failure"
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
