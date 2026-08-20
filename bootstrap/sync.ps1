#!/usr/bin/env pwsh
[CmdletBinding()]
param(
    [string]$RepoRoot = (Split-Path -Parent $PSScriptRoot),
    [string]$CodexSkillsDir = (Join-Path $env:USERPROFILE ".agents\skills"),
    [string]$AntigravityPluginsDir = (Join-Path $env:USERPROFILE ".gemini\config\plugins"),
    [switch]$SkipClaudeCode,
    [switch]$Import  # when set, only defines functions (used by sync.test.ps1)
)

$ErrorActionPreference = "Stop"

function Get-PluginNames {
    param([string]$RepoRoot)
    Get-ChildItem (Join-Path $RepoRoot "plugins") -Directory | Select-Object -ExpandProperty Name
}

function New-OrRepairJunction {
    param([string]$LinkPath, [string]$TargetPath)

    if (Test-Path $LinkPath) {
        $existing = Get-Item $LinkPath
        if (-not $existing.LinkType) {
            throw "Refusing to overwrite '$LinkPath' — it exists and is not a link this script manages."
        }
        $currentTarget = (Get-Item $LinkPath).Target
        if ($currentTarget -eq $TargetPath) {
            return  # already correct, idempotent no-op
        }
        Remove-Item $LinkPath -Force
    }

    try {
        New-Item -ItemType Junction -Path $LinkPath -Target $TargetPath -Force | Out-Null
    } catch {
        throw "Failed to create junction '$LinkPath' -> '$TargetPath': $($_.Exception.Message)"
    }
}

function Sync-CodexSkills {
    param([string]$RepoRoot, [string]$CodexSkillsDir)

    if (-not (Test-Path $CodexSkillsDir)) {
        New-Item -ItemType Directory -Path $CodexSkillsDir -Force | Out-Null
    }

    foreach ($plugin in (Get-PluginNames -RepoRoot $RepoRoot)) {
        $skillsRoot = Join-Path $RepoRoot "plugins\$plugin\skills"
        if (-not (Test-Path $skillsRoot)) { continue }
        Get-ChildItem $skillsRoot -Directory | ForEach-Object {
            $target = $_.FullName
            $link = Join-Path $CodexSkillsDir $_.Name
            New-OrRepairJunction -LinkPath $link -TargetPath $target
        }
    }
}

function Sync-AntigravityPlugins {
    param([string]$RepoRoot, [string]$AntigravityPluginsDir)

    if (-not (Test-Path $AntigravityPluginsDir)) {
        New-Item -ItemType Directory -Path $AntigravityPluginsDir -Force | Out-Null
    }

    foreach ($plugin in (Get-PluginNames -RepoRoot $RepoRoot)) {
        $target = Join-Path $RepoRoot "plugins\$plugin"
        $link = Join-Path $AntigravityPluginsDir $plugin
        New-OrRepairJunction -LinkPath $link -TargetPath $target
    }
}

function Sync-ClaudeCodeMarketplace {
    param([string]$RepoRoot)

    & claude plugin marketplace add $RepoRoot
    if ($LASTEXITCODE -ne 0) {
        throw "claude plugin marketplace add '$RepoRoot' failed with exit code $LASTEXITCODE"
    }

    foreach ($plugin in (Get-PluginNames -RepoRoot $RepoRoot)) {
        & claude plugin install "$plugin@agent-extensions"
        if ($LASTEXITCODE -ne 0) {
            throw "claude plugin install '$plugin@agent-extensions' failed with exit code $LASTEXITCODE"
        }
    }
}

if ($Import) { return }

Sync-CodexSkills -RepoRoot $RepoRoot -CodexSkillsDir $CodexSkillsDir
Sync-AntigravityPlugins -RepoRoot $RepoRoot -AntigravityPluginsDir $AntigravityPluginsDir
if (-not $SkipClaudeCode) {
    Sync-ClaudeCodeMarketplace -RepoRoot $RepoRoot
}

Write-Output "Sync complete."
