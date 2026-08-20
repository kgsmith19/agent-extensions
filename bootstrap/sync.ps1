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

        if (Test-Path $dest) {
            try {
                Remove-Item $dest -Recurse -Force
            } catch {
                $failures += "Marketplace '$($mp.name)': failed to clear existing vendor-cache directory '$dest' — $($_.Exception.Message)"
                continue
            }
        }
        try {
            New-Item -ItemType Directory -Path $dest -Force | Out-Null
        } catch {
            $failures += "Marketplace '$($mp.name)': failed to create vendor-cache directory '$dest' — $($_.Exception.Message)"
            continue
        }

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

function ConvertTo-TomlString {
    param([string]$Value)
    $escaped = $Value -replace '\\', '\\' -replace '"', '\"'
    return '"' + $escaped + '"'
}

function ConvertTo-CodexMcpToml {
    param($McpServers)

    $stdioAllowed = @('command', 'args', 'env')
    $httpAllowed = @('url', 'headers')
    $lines = @()

    foreach ($prop in $McpServers.PSObject.Properties) {
        $serverName = $prop.Name
        $server = $prop.Value
        $propNames = @($server.PSObject.Properties.Name)
        $hasCommand = $propNames -contains 'command'
        $hasUrl = $propNames -contains 'url'

        if (-not $hasCommand -and -not $hasUrl) {
            throw "MCP server '$serverName' has neither 'command' (stdio) nor 'url' (http) — unrecognized server shape."
        }
        if ($hasCommand -and $hasUrl) {
            throw "MCP server '$serverName' has both 'command' and 'url' — ambiguous transport, cannot translate."
        }

        $allowed = if ($hasCommand) { $stdioAllowed } else { $httpAllowed }
        $unknown = @($propNames | Where-Object { $allowed -notcontains $_ })
        if ($unknown.Count -gt 0) {
            throw "MCP server '$serverName' has unrecognized field(s): $($unknown -join ', ')."
        }

        $lines += "[mcp_servers.$serverName]"
        if ($hasCommand) {
            $lines += "command = $(ConvertTo-TomlString $server.command)"
            if ($propNames -contains 'args') {
                $argStrings = @($server.args | ForEach-Object { ConvertTo-TomlString $_ })
                $lines += "args = [" + ($argStrings -join ", ") + "]"
            }
            if ($propNames -contains 'env') {
                $envPairs = @($server.env.PSObject.Properties | ForEach-Object {
                    "$($_.Name) = $(ConvertTo-TomlString $_.Value)"
                })
                $lines += "env = { " + ($envPairs -join ", ") + " }"
            }
        } else {
            $lines += "url = $(ConvertTo-TomlString $server.url)"
            if ($propNames -contains 'headers') {
                $headerPairs = @($server.headers.PSObject.Properties | ForEach-Object {
                    "$($_.Name) = $(ConvertTo-TomlString $_.Value)"
                })
                $lines += "http_headers = { " + ($headerPairs -join ", ") + " }"
            }
        }
        $lines += ""
    }
    return ($lines -join "`n").Trim()
}

function ConvertTo-AntigravityMcpConfig {
    param($McpServers)

    $stdioAllowed = @('command', 'args', 'env')
    $httpAllowed = @('url', 'headers')

    foreach ($prop in $McpServers.PSObject.Properties) {
        $serverName = $prop.Name
        $server = $prop.Value
        $propNames = @($server.PSObject.Properties.Name)
        $hasCommand = $propNames -contains 'command'
        $hasUrl = $propNames -contains 'url'

        if (-not $hasCommand -and -not $hasUrl) {
            throw "MCP server '$serverName' has neither 'command' (stdio) nor 'url' (http) — unrecognized server shape."
        }
        if ($hasCommand -and $hasUrl) {
            throw "MCP server '$serverName' has both 'command' and 'url' — ambiguous transport, cannot translate."
        }
        $allowed = if ($hasCommand) { $stdioAllowed } else { $httpAllowed }
        $unknown = @($propNames | Where-Object { $allowed -notcontains $_ })
        if ($unknown.Count -gt 0) {
            throw "MCP server '$serverName' has unrecognized field(s): $($unknown -join ', ')."
        }
    }

    $wrapped = [PSCustomObject]@{ mcpServers = $McpServers }
    return ($wrapped | ConvertTo-Json -Depth 10)
}

function Merge-CodexMcpConfig {
    param([string]$ConfigPath, [hashtable]$TomlByPlugin)

    $beginMarker = "# >>> agent-extensions managed mcp_servers (do not edit within this block) >>>"
    $endMarker = "# <<< agent-extensions managed mcp_servers <<<"

    $existingLines = @()
    if (Test-Path $ConfigPath) {
        $existingLines = @(Get-Content $ConfigPath)
    }

    $beginIdx = -1
    $endIdx = -1
    for ($i = 0; $i -lt $existingLines.Count; $i++) {
        if ($existingLines[$i] -eq $beginMarker) { $beginIdx = $i; break }
    }
    if ($beginIdx -ge 0) {
        for ($i = $beginIdx + 1; $i -lt $existingLines.Count; $i++) {
            if ($existingLines[$i] -eq $endMarker) { $endIdx = $i; break }
        }
    }

    if ($beginIdx -ge 0 -and $endIdx -ge 0) {
        $beforeLines = if ($beginIdx -gt 0) { @($existingLines[0..($beginIdx - 1)]) } else { @() }
        $afterLines = if ($endIdx + 1 -lt $existingLines.Count) { @($existingLines[($endIdx + 1)..($existingLines.Count - 1)]) } else { @() }
        $before = ($beforeLines -join "`n").TrimEnd()
        $after = ($afterLines -join "`n").TrimStart()
        $parts = @($before, $after) | Where-Object { $_ -ne "" }
        $existing = $parts -join "`n`n"
    } else {
        $existing = ($existingLines -join "`n").TrimEnd()
    }

    $blockLines = @($beginMarker)
    foreach ($plugin in ($TomlByPlugin.Keys | Sort-Object)) {
        $toml = $TomlByPlugin[$plugin]
        if ([string]::IsNullOrWhiteSpace($toml)) { continue }
        $blockLines += "# plugin: $plugin"
        $blockLines += $toml.TrimEnd()
        $blockLines += ""
    }
    $blockLines += $endMarker
    $block = ($blockLines -join "`n")

    $parentDir = Split-Path -Parent $ConfigPath
    if (-not (Test-Path $parentDir)) {
        New-Item -ItemType Directory -Path $parentDir -Force | Out-Null
    }

    $final = if ([string]::IsNullOrWhiteSpace($existing)) { "$block`n" } else { "$existing`n`n$block`n" }
    Set-Content -Path $ConfigPath -Value $final -NoNewline
}

function Write-AntigravityMcpConfig {
    param([string]$PluginStagedDir, [string]$JsonContent)
    if ([string]::IsNullOrWhiteSpace($JsonContent)) { return }
    if (-not (Test-Path $PluginStagedDir)) {
        New-Item -ItemType Directory -Path $PluginStagedDir -Force | Out-Null
    }
    $target = Join-Path $PluginStagedDir "mcp_config.json"
    Set-Content -Path $target -Value $JsonContent -NoNewline
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
