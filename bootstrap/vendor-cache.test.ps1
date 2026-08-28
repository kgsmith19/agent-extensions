# bootstrap/vendor-cache.test.ps1
#!/usr/bin/env pwsh
$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $PSScriptRoot
. (Join-Path $PSScriptRoot "sync.ps1") -Import
. (Join-Path $RepoRoot "bootstrap\tests\fixtures\marketplace-fixture.ps1")

$scratch = Join-Path ([System.IO.Path]::GetTempPath()) "agent-extensions-vendorcache-$(Get-Random)"
$fixtureRepo = Join-Path $scratch "fixture-marketplace"
$declareRoot = Join-Path $scratch "declare-root"
$vendorCache = Join-Path $scratch "vendor-cache"
New-Item -ItemType Directory -Path $declareRoot -Force | Out-Null

$repoDir = Join-Path $scratch "plugin-repo"
$repoSha = New-FixturePluginRepo -DestDir $repoDir
$sha = New-FixtureMarketplace -DestDir $fixtureRepo -PluginRepoDir $repoDir -PluginRepoSha $repoSha

$manifest = @{
    marketplaces = @(
        @{ name = "fixture-mp"; repo = $fixtureRepo; pinnedCommit = $sha; plugins = @("alpha-skills") }
    )
} | ConvertTo-Json -Depth 10
$bootstrapDir = Join-Path $declareRoot "bootstrap"
New-Item -ItemType Directory -Path $bootstrapDir -Force | Out-Null
Set-Content -Path (Join-Path $bootstrapDir "external-marketplaces.json") -Value $manifest

$failures = @()

# --- Resolve-MarketplaceUrl ---
if ((Resolve-MarketplaceUrl -Repo "anthropics/claude-plugins-official") -ne "https://github.com/anthropics/claude-plugins-official.git") {
    $failures += "Resolve-MarketplaceUrl did not expand owner/repo shorthand correctly"
}
if ((Resolve-MarketplaceUrl -Repo $fixtureRepo) -ne $fixtureRepo) {
    $failures += "Resolve-MarketplaceUrl should pass through a local path unchanged"
}

# --- Get-ExternalMarketplaces ---
$parsed = Get-ExternalMarketplaces -RepoRoot $declareRoot
if ($parsed.Count -ne 1 -or $parsed[0].name -ne "fixture-mp") {
    $failures += "Get-ExternalMarketplaces did not parse the declared fixture marketplace"
}

# --- Sync-VendorCache: initial clone ---
$cloneFailures = Sync-VendorCache -RepoRoot $declareRoot -VendorCacheDir $vendorCache
if ($cloneFailures.Count -gt 0) {
    $failures += "Sync-VendorCache reported failures on a valid fixture: $($cloneFailures -join '; ')"
}
$clonedFile = Join-Path $vendorCache "fixture-mp\plugins\alpha-skills\skills\greet\SKILL.md"
if (-not (Test-Path $clonedFile)) {
    $failures += "Vendor cache clone did not produce expected file: $clonedFile"
}

# --- Idempotency: mark the clone, re-run, confirm no re-clone ---
$marker = Join-Path $vendorCache "fixture-mp\MARKER.txt"
Set-Content -Path $marker -Value "should survive a no-op re-sync"
Sync-VendorCache -RepoRoot $declareRoot -VendorCacheDir $vendorCache | Out-Null
if (-not (Test-Path $marker)) {
    $failures += "Sync-VendorCache re-cloned an already-pinned marketplace (marker file was wiped)"
}

# --- Missing pinned commit: loud, reported failure, not silent ---
$badManifest = @{
    marketplaces = @(
        @{ name = "fixture-mp"; repo = $fixtureRepo; pinnedCommit = "0000000000000000000000000000000000000bad"; plugins = @("alpha-skills") }
    )
} | ConvertTo-Json -Depth 10
Set-Content -Path (Join-Path $bootstrapDir "external-marketplaces.json") -Value $badManifest
$badVendorCache = Join-Path $scratch "vendor-cache-bad"
$badFailures = Sync-VendorCache -RepoRoot $declareRoot -VendorCacheDir $badVendorCache
if ($badFailures.Count -eq 0) {
    $failures += "Sync-VendorCache silently succeeded on a nonexistent pinned commit"
}

Remove-Item -Recurse -Force $scratch

if ($failures.Count -gt 0) {
    Write-Output "FAIL: vendor-cache clone/pin"
    $failures | ForEach-Object { Write-Output "  - $_" }
    exit 1
}

Write-Output "PASS: vendor-cache clone/pin, idempotent, loud on bad pin"
exit 0
