#!/usr/bin/env pwsh
$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $PSScriptRoot
. (Join-Path $PSScriptRoot "sync.ps1") -Import

$scratch = Join-Path ([System.IO.Path]::GetTempPath()) "agent-extensions-accounttest-$(Get-Random)"
$declareRoot = Join-Path $scratch "declare-root"
$stagedDir = Join-Path $scratch "staged"
New-Item -ItemType Directory -Path (Join-Path $declareRoot "bootstrap") -Force | Out-Null
New-Item -ItemType Directory -Path (Join-Path $declareRoot "plugins\demo\skills\greet") -Force | Out-Null
Set-Content -Path (Join-Path $declareRoot "plugins\demo\skills\greet\SKILL.md") -Value "# Greet"

@'
{
  "skills": [
    { "name": "greet", "source": "plugins/demo/skills/greet" },
    { "name": "missing-skill", "source": "plugins/demo/skills/nope" }
  ],
  "connectors": [{ "name": "some-connector", "description": "d" }]
}
'@ | Set-Content -Path (Join-Path $declareRoot "bootstrap\account-manifest.json")

@'
{ "skills": [{ "name": "old-skill", "source": "x" }], "connectors": [] }
'@ | Set-Content -Path (Join-Path $declareRoot "bootstrap\account-manifest.last-applied.json")

$failures = @()

# --- Staging: valid skill copied as a real file, not a symlink ---
$stdout = (Sync-AccountBundles -RepoRoot $declareRoot -StagedDir $stagedDir 6>&1 | Out-String)
$reported = Sync-AccountBundles -RepoRoot $declareRoot -StagedDir $stagedDir
if (@($reported).Count -eq 0) {
    $failures += "Expected Sync-AccountBundles to report a failure because of missing-skill"
} elseif (-not ($reported -match "missing-skill")) {
    $failures += "Expected a reported failure mentioning missing-skill"
}

$greetFile = Join-Path $stagedDir "greet\SKILL.md"
if (-not (Test-Path $greetFile)) {
    $failures += "greet's SKILL.md was not staged"
} elseif ((Get-Item $greetFile).LinkType) {
    $failures += "Staged bundle must be a real copy, not a link — claude.ai's upload flow does not resolve links"
}

# --- Checklist: diffs current manifest against last-applied ---
if ($stdout -notmatch "ADD:") { $failures += "Expected an ADD section in the checklist" }
if ($stdout -notmatch "skill: greet") { $failures += "Expected 'greet' to be listed under ADD" }
if ($stdout -notmatch "connector: some-connector") { $failures += "Expected 'some-connector' to be listed under ADD" }
if ($stdout -notmatch "REMOVE:") { $failures += "Expected a REMOVE section (old-skill is in last-applied but not current)" }
if ($stdout -notmatch "skill: old-skill") { $failures += "Expected 'old-skill' to be listed under REMOVE" }
if ($stdout -match '"(skill|connector):') { $failures += "Checklist entries must be plain text, not JSON-quoted strings" }

# --- Confirm-AccountApplied: snapshots current manifest over last-applied ---
Confirm-AccountApplied -RepoRoot $declareRoot | Out-Null
$curText = Get-Content (Join-Path $declareRoot "bootstrap\account-manifest.json") -Raw
$lastText = Get-Content (Join-Path $declareRoot "bootstrap\account-manifest.last-applied.json") -Raw
if ($curText -ne $lastText) {
    $failures += "Confirm-AccountApplied did not make last-applied match the current manifest"
}

# --- After confirming, the checklist reports up to date ---
$upToDateOut = (Sync-AccountBundles -RepoRoot $declareRoot -StagedDir $stagedDir 6>&1 | Out-String)
if ($upToDateOut -notmatch "up to date") {
    $failures += "Expected 'up to date' after Confirm-AccountApplied, got: $upToDateOut"
}

Remove-Item -Recurse -Force $scratch

if ($failures.Count -gt 0) {
    Write-Output "FAIL: account bundles / checklist / confirm"
    $failures | ForEach-Object { Write-Output "  - $_" }
    exit 1
}

Write-Output "PASS: account bundles stage, checklist ADD/REMOVE, confirm-applied snapshot"
exit 0
