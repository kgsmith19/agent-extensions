#!/usr/bin/env pwsh
$ErrorActionPreference = "Stop"
$here = $PSScriptRoot
. (Join-Path $here "marketplace-fixture.ps1")

$scratch = Join-Path ([System.IO.Path]::GetTempPath()) "agent-extensions-fixturetest-$(Get-Random)"
$sha = New-FixtureMarketplace -DestDir $scratch

$failures = @()

if ($sha -notmatch '^[0-9a-f]{40}$') {
    $failures += "New-FixtureMarketplace did not return a 40-char commit SHA, got: '$sha'"
}

Push-Location $scratch
try {
    $actualSha = (& git rev-parse HEAD).Trim()
    if ($actualSha -ne $sha) {
        $failures += "Returned SHA '$sha' does not match repo HEAD '$actualSha'"
    }
} finally {
    Pop-Location
}

foreach ($expected in @(
    "alpha-skills\skills\greet\SKILL.md",
    "beta-mcp-stdio\.mcp.json",
    "gamma-mcp-http\.mcp.json",
    "delta-malformed\.mcp.json"
)) {
    if (-not (Test-Path (Join-Path $scratch $expected))) {
        $failures += "Expected fixture file missing: $expected"
    }
}

Remove-Item -Recurse -Force $scratch

if ($failures.Count -gt 0) {
    Write-Output "FAIL: marketplace-fixture builder"
    $failures | ForEach-Object { Write-Output "  - $_" }
    exit 1
}

Write-Output "PASS: marketplace-fixture builder produces expected content and commit"
exit 0
