#!/usr/bin/env pwsh
# Verifies every vendored skill has a SKILL.md whose frontmatter `name`
# matches its folder name, and that LICENSE + VENDORED-FROM exist.
param(
    [string]$RepoRoot = (Split-Path -Parent $PSScriptRoot)
)

$ErrorActionPreference = "Stop"
$failures = @()

$expected = @{
    "anthropic-product-skills" = @("canvas-design", "web-artifacts-builder")
    "general-skills"           = @("skill-creator")
}

if (-not (Test-Path (Join-Path $RepoRoot "LICENSE"))) {
    $failures += "Missing $RepoRoot\LICENSE"
}

foreach ($plugin in $expected.Keys) {
    $skillsDir = Join-Path $RepoRoot "plugins\$plugin\skills"
    $vendoredFrom = Join-Path $skillsDir "VENDORED-FROM"

    if (-not (Test-Path $vendoredFrom)) {
        $failures += "Missing $vendoredFrom"
    }

    foreach ($skill in $expected[$plugin]) {
        $skillMd = Join-Path $skillsDir "$skill\SKILL.md"
        if (-not (Test-Path $skillMd)) {
            $failures += "Missing $skillMd"
            continue
        }
        $content = Get-Content $skillMd -Raw
        if ($content -notmatch '(?ms)^---\s*.*?^name:\s*([^\r\n]+)') {
            $failures += "$skillMd has no frontmatter 'name' field"
            continue
        }
        $foundName = $Matches[1].Trim()
        if ($foundName -ne $skill) {
            $failures += "$skillMd frontmatter name '$foundName' does not match folder name '$skill'"
        }
    }
}

if ($failures.Count -gt 0) {
    Write-Output "FAIL: content verification"
    $failures | ForEach-Object { Write-Output "  - $_" }
    exit 1
}

Write-Output "PASS: all vendored skills present, named correctly, LICENSE and VENDORED-FROM present"
exit 0
