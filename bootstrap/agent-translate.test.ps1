#!/usr/bin/env pwsh
$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $PSScriptRoot
. (Join-Path $PSScriptRoot "sync.ps1") -Import

$scratch = Join-Path ([System.IO.Path]::GetTempPath()) "agent-extensions-agenttest-$(Get-Random)"
New-Item -ItemType Directory -Path $scratch -Force | Out-Null
$failures = @()

# --- Simple case ---
@'
---
name: simple-agent
description: Does a simple thing.
model: opus
tools: Read, Grep
color: red
---

You are a simple agent. Do the thing.
'@ | Set-Content -Path (Join-Path $scratch "simple.md")

$toml = ConvertTo-CodexAgentToml -AgentMdPath (Join-Path $scratch "simple.md") -PluginName "myplugin"
if ($null -eq $toml) { $failures += "Expected ConvertTo-CodexAgentToml to succeed on the simple case" }
if (($toml -split "`n")[0] -ne 'name = "myplugin-simple-agent"') {
    $failures += "Codex TOML name should be plugin-qualified, got: $(($toml -split "`n")[0])"
}
if ($toml -match '(?im)^model|^tools') {
    $failures += "Codex TOML must not carry over 'model' or 'tools' — no principled cross-provider mapping exists"
}
if ($toml -notmatch "You are a simple agent") {
    $failures += "Codex TOML developer_instructions should contain the markdown body"
}

$md = ConvertTo-AntigravityAgentMd -AgentMdPath (Join-Path $scratch "simple.md") -PluginName "myplugin"
if ($null -eq $md) { $failures += "Expected ConvertTo-AntigravityAgentMd to succeed on the simple case" }
if ($md -notmatch 'name: "myplugin-simple-agent"') {
    $failures += "Antigravity MD name should be plugin-qualified"
}
if ($md -match '(?im)^model:|^tools:|^color:') {
    $failures += "Antigravity MD must not carry over 'model', 'tools', or 'color'"
}

# --- No name field: defaults to filename ---
@'
---
description: Has no name field.
---

Body text.
'@ | Set-Content -Path (Join-Path $scratch "unnamed-agent.md")
$toml2 = ConvertTo-CodexAgentToml -AgentMdPath (Join-Path $scratch "unnamed-agent.md") -PluginName "myplugin"
if (($toml2 -split "`n")[0] -ne 'name = "myplugin-unnamed-agent"') {
    $failures += "Expected missing 'name:' to default to the filename, got: $(($toml2 -split "`n")[0])"
}

# --- Block-scalar description: declared failure, not a guess ---
@'
---
name: block-agent
description: |
  This is a multi-line
  block scalar description.
---

Body.
'@ | Set-Content -Path (Join-Path $scratch "block.md")
$blockResult = ConvertTo-CodexAgentToml -AgentMdPath (Join-Path $scratch "block.md") -PluginName "myplugin"
if ($null -ne $blockResult) {
    $failures += "Expected ConvertTo-CodexAgentToml to decline a block-scalar description, not guess at it"
}

# --- Single-quoted description with embedded double quotes ---
@"
---
name: quoted-agent
description: 'Say "hello" to the user, then continue.'
---

Body.
"@ | Set-Content -Path (Join-Path $scratch "quoted.md")
$toml3 = ConvertTo-CodexAgentToml -AgentMdPath (Join-Path $scratch "quoted.md") -PluginName "myplugin"
if ($toml3 -notmatch 'Say \\"hello\\" to the user') {
    $failures += "Expected embedded double quotes to be preserved and properly escaped"
}

# --- Collision safety: two different plugins with an agent of the same name ---
New-Item -ItemType Directory -Path (Join-Path $scratch "plugin-a\agents") -Force | Out-Null
New-Item -ItemType Directory -Path (Join-Path $scratch "plugin-b\agents") -Force | Out-Null
@"
---
name: reviewer
description: Plugin A's reviewer.
---
Body A.
"@ | Set-Content -Path (Join-Path $scratch "plugin-a\agents\reviewer.md")
@"
---
name: reviewer
description: Plugin B's reviewer.
---
Body B.
"@ | Set-Content -Path (Join-Path $scratch "plugin-b\agents\reviewer.md")
$codexAgentsOut = Join-Path $scratch "codex-agents-out"
Sync-PluginAgentsCodex -PluginDir (Join-Path $scratch "plugin-a") -PluginName "plugin-a" -CodexAgentsDir $codexAgentsOut | Out-Null
Sync-PluginAgentsCodex -PluginDir (Join-Path $scratch "plugin-b") -PluginName "plugin-b" -CodexAgentsDir $codexAgentsOut | Out-Null
if (-not (Test-Path (Join-Path $codexAgentsOut "plugin-a-reviewer.toml")) -or -not (Test-Path (Join-Path $codexAgentsOut "plugin-b-reviewer.toml"))) {
    $failures += "Expected both plugins' same-named agent to produce distinct qualified files"
}

Remove-Item -Recurse -Force $scratch

if ($failures.Count -gt 0) {
    Write-Output "FAIL: agent translation (Codex TOML / Antigravity MD)"
    $failures | ForEach-Object { Write-Output "  - $_" }
    exit 1
}

Write-Output "PASS: agent translation — simple case, name defaulting, block-scalar decline, quote handling, cross-plugin collision safety"
exit 0
