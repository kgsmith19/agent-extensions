#!/usr/bin/env pwsh
param(
    [string]$RepoRoot = (Split-Path $PSScriptRoot -Parent),
    [string]$CodexSkillsDir = (Join-Path $HOME ".agents\skills"),
    [string]$AntigravityPluginsDir = (Join-Path $HOME ".gemini\config\plugins"),
    [string]$CodexConfigPath = (Join-Path $HOME ".codex\config.toml")
)

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "sync.ps1") -Import

$vendorCacheDir = Join-Path $RepoRoot ".vendor-cache"
$problems = @()
$expectedSkills = 0
$expectedMcp = 0
$expectedMcpServers = @()

foreach ($mp in (Get-ExternalMarketplaces -RepoRoot $RepoRoot)) {
    foreach ($declared in (Get-DeclaredPlugins -Marketplace $mp)) {
        $r = Resolve-PluginDir -RepoRoot $RepoRoot -VendorCacheDir $vendorCacheDir -Marketplace $mp -DeclaredPlugin $declared
        if ($r.Failure -ne "") { $problems += $r.Failure; continue }

        $skillsRoot = Join-Path $r.Dir "skills"
        if (Test-Path $skillsRoot) {
            foreach ($s in (Get-ChildItem $skillsRoot -Directory)) {
                $expectedSkills++
                if (-not (Test-Path (Join-Path $CodexSkillsDir $s.Name))) {
                    $problems += "Codex: skill '$($s.Name)' (from '$($declared.Name)') is not linked"
                }
            }
        }
        $mcpPath = Join-Path $r.Dir ".mcp.json"
        if (Test-Path $mcpPath) {
            $expectedMcp++
            try {
                $mcpJson = Get-Content $mcpPath -Raw | ConvertFrom-Json
                foreach ($server in (Get-McpServers -McpJson $mcpJson).PSObject.Properties.Name) {
                    $expectedMcpServers += $server
                }
            } catch {
                $problems += "Plugin '$($declared.Name)' (from '$($mp.name)'): could not parse MCP config '$mcpPath' — $($_.Exception.Message)"
            }
        }

        if (-not (Test-Path (Join-Path $AntigravityPluginsDir $declared.Name))) {
            $problems += "Antigravity: plugin '$($declared.Name)' is not linked"
        }
    }
}

$actualMcp = 0
if (Test-Path $CodexConfigPath) {
    $actualMcp = @(Select-String -Path $CodexConfigPath -Pattern '^\[mcp_servers\.[^\].]+\]$' -AllMatches).Count
}

Write-Output "Expected skills: $expectedSkills   Expected plugins shipping MCP: $expectedMcp"
Write-Output "Codex [mcp_servers.*] entries present: $actualMcp"

if ($problems.Count -gt 0) {
    Write-Output ""
    Write-Output "VERIFY FAILED ($($problems.Count) problems):"
    $problems | ForEach-Object { Write-Output "  - $_" }
    exit 1
}
if (Test-Path $CodexConfigPath) {
    $configText = Get-Content $CodexConfigPath -Raw
    foreach ($server in ($expectedMcpServers | Sort-Object -Unique)) {
        if ($configText -notmatch "(?m)^\[mcp_servers\.$([regex]::Escape($server))\]$") {
            Write-Output "VERIFY FAILED: Codex MCP server '$server' is missing from '$CodexConfigPath'."
            exit 1
        }
    }
} elseif ($expectedMcpServers.Count -gt 0) {
    Write-Output "VERIFY FAILED: Codex config '$CodexConfigPath' does not exist."
    exit 1
}
if ($expectedSkills -eq 0) {
    Write-Output "VERIFY FAILED: resolved zero skills across the whole roster — resolution is not working."
    exit 1
}
Write-Output "VERIFY OK"
