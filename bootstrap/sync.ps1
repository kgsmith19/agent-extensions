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

function Get-ExternalMarketplaces {
    param([string]$RepoRoot)
    $path = Join-Path $RepoRoot "bootstrap\external-marketplaces.json"
    if (-not (Test-Path $path)) { return @() }
    $json = Get-Content $path -Raw | ConvertFrom-Json
    if (-not $json.PSObject.Properties['marketplaces']) { return @() }
    return @($json.marketplaces)
}

function Resolve-MarketplaceUrl {
    param([string]$Repo)
    if ($Repo -match '^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$') {
        return "https://github.com/$Repo.git"
    }
    return $Repo
}

function Sync-VendorCache {
    param([string]$RepoRoot, [string]$VendorCacheDir)

    $marketplaces = Get-ExternalMarketplaces -RepoRoot $RepoRoot
    $failures = @()

    foreach ($mp in $marketplaces) {
        $dest = Join-Path $VendorCacheDir $mp.name
        $currentSha = $null
        if (Test-Path (Join-Path $dest ".git")) {
            Push-Location $dest
            try { $currentSha = (& git rev-parse HEAD 2>$null | Out-String).Trim() } finally { Pop-Location }
        }
        if ($currentSha -eq $mp.pinnedCommit) { continue }

        if (Test-Path $dest) { Remove-Item $dest -Recurse -Force }
        New-Item -ItemType Directory -Path $dest -Force | Out-Null

        $url = Resolve-MarketplaceUrl -Repo $mp.repo
        Push-Location $dest
        try {
            & git init -q 2>&1 | Out-Null
            & git remote add origin $url 2>&1 | Out-Null
            & git fetch --depth 1 origin $mp.pinnedCommit 2>&1 | Out-Null
            if ($LASTEXITCODE -ne 0) {
                $failures += "Marketplace '$($mp.name)': failed to fetch commit '$($mp.pinnedCommit)' from '$url' — it may no longer exist upstream."
                continue
            }
            & git checkout -q FETCH_HEAD 2>&1 | Out-Null
            if ($LASTEXITCODE -ne 0) {
                $failures += "Marketplace '$($mp.name)': failed to check out pinned commit '$($mp.pinnedCommit)'."
            }
        } finally {
            Pop-Location
        }
    }

    return $failures
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

    return $true
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

    return $true
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
