#!/usr/bin/env pwsh
# Exercises Sync-CodexSkills and Sync-AntigravityPlugins against a scratch
# directory tree — never touches the real ~/.agents or ~/.gemini.
$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $PSScriptRoot
. (Join-Path $PSScriptRoot "sync.ps1") -Import

$scratch = Join-Path ([System.IO.Path]::GetTempPath()) "agent-extensions-synctest-$(Get-Random)"
$codexDir = Join-Path $scratch "agents-skills"
$antigravityDir = Join-Path $scratch "gemini-plugins"
New-Item -ItemType Directory -Path $codexDir -Force | Out-Null
New-Item -ItemType Directory -Path $antigravityDir -Force | Out-Null

$failures = @()

# --- Codex linking ---
Sync-CodexSkills -RepoRoot $RepoRoot -CodexSkillsDir $codexDir

foreach ($skill in @("canvas-design", "web-artifacts-builder", "skill-creator")) {
    $linkPath = Join-Path $codexDir $skill
    $item = Get-Item $linkPath -ErrorAction SilentlyContinue
    if (-not $item) {
        $failures += "Codex: $linkPath does not exist"
    } elseif (-not $item.LinkType) {
        $failures += "Codex: $linkPath exists but is not a junction/link (would be a stale copy, not a live link)"
    }
}

# --- Idempotency: run again, expect no error and no duplicate/broken state ---
Sync-CodexSkills -RepoRoot $RepoRoot -CodexSkillsDir $codexDir
$recheck = Get-Item (Join-Path $codexDir "skill-creator") -ErrorAction SilentlyContinue
if (-not $recheck -or -not $recheck.LinkType) {
    $failures += "Codex: second sync run broke the skill-creator link"
}

# --- Antigravity linking ---
Sync-AntigravityPlugins -RepoRoot $RepoRoot -AntigravityPluginsDir $antigravityDir

foreach ($plugin in @("anthropic-product-skills", "general-skills")) {
    $linkPath = Join-Path $antigravityDir $plugin
    $item = Get-Item $linkPath -ErrorAction SilentlyContinue
    if (-not $item) {
        $failures += "Antigravity: $linkPath does not exist"
    } elseif (-not $item.LinkType) {
        $failures += "Antigravity: $linkPath exists but is not a junction/link"
    }
}

# --- Live-link requirement: editing the repo file must be visible through the link ---
$marker = "sync-test-marker-$(Get-Random)"
$testFile = Join-Path $RepoRoot "plugins\general-skills\skills\skill-creator\SKILL.md"
Add-Content -Path $testFile -Value "<!-- $marker -->"
$viaLink = Get-Content (Join-Path $codexDir "skill-creator\SKILL.md") -Raw
if ($viaLink -notmatch [regex]::Escape($marker)) {
    $failures += "Codex link for skill-creator is a copy, not a live link (marker not visible through it)"
}
(Get-Content $testFile) | Where-Object { $_ -notmatch [regex]::Escape($marker) } | Set-Content $testFile

Remove-Item -Recurse -Force $scratch

if ($failures.Count -gt 0) {
    Write-Output "FAIL: sync.ps1 Codex/Antigravity linking"
    $failures | ForEach-Object { Write-Output "  - $_" }
    exit 1
}

Write-Output "PASS: Codex and Antigravity linking, idempotent, live-linked"
exit 0
