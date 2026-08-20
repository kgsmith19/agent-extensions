#!/usr/bin/env pwsh
param(
    [string]$RepoRoot = (Split-Path -Parent $PSScriptRoot)
)

$ErrorActionPreference = "Stop"
$failures = @()

$agentsPath = Join-Path $RepoRoot "AGENTS.md"
if (-not (Test-Path $agentsPath)) {
    $failures += "Missing $agentsPath"
} elseif ((Get-Content $agentsPath -Raw).Trim().Length -eq 0) {
    $failures += "$agentsPath is empty"
}

$claudePath = Join-Path $RepoRoot "CLAUDE.md"
if (-not (Test-Path $claudePath)) {
    $failures += "Missing $claudePath"
} elseif ((Get-Content $claudePath -Raw) -notmatch "AGENTS\.md") {
    $failures += "$claudePath does not reference AGENTS.md"
}

$geminiPath = Join-Path $RepoRoot "GEMINI.md"
if (-not (Test-Path $geminiPath)) {
    $failures += "Missing $geminiPath"
} elseif ((Get-Content $geminiPath -Raw) -notmatch "AGENTS\.md") {
    $failures += "$geminiPath does not reference AGENTS.md"
}

$readmePath = Join-Path $RepoRoot "README.md"
if (-not (Test-Path $readmePath)) {
    $failures += "Missing $readmePath"
}

if ($failures.Count -gt 0) {
    Write-Output "FAIL: pointer file verification"
    $failures | ForEach-Object { Write-Output "  - $_" }
    exit 1
}

Write-Output "PASS: AGENTS.md, CLAUDE.md, GEMINI.md, README.md all present and correctly linked"
exit 0
