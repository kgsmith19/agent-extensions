#!/usr/bin/env pwsh
$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $PSScriptRoot
. (Join-Path $PSScriptRoot "sync.ps1") -Import

$scratch = Join-Path ([System.IO.Path]::GetTempPath()) "agent-extensions-hooktest-$(Get-Random)"
New-Item -ItemType Directory -Path $scratch -Force | Out-Null
$failures = @()

@'
{
  "hooks": {
    "PreToolUse": [
      { "hooks": [{ "type": "command", "command": "python3 \"${CLAUDE_PLUGIN_ROOT}/hooks/pre.py\"", "timeout": 10 }] }
    ],
    "UserPromptSubmit": [
      { "hooks": [{ "type": "command", "command": "echo hi" }] }
    ],
    "SessionStart": [
      { "matcher": "startup", "hooks": [{ "type": "command", "command": "echo start", "shell": "bash", "async": false }] }
    ],
    "NotAnEvent": [
      { "hooks": [{ "type": "command", "command": "echo unknown" }] }
    ]
  }
}
'@ | Set-Content -Path (Join-Path $scratch "hooks.json")

# --- Codex: PreToolUse, UserPromptSubmit, SessionStart all overlap; NotAnEvent must be dropped ---
$codexHooks = ConvertTo-CodexHooksJson -HooksJsonPath (Join-Path $scratch "hooks.json") -PluginDir "/plugins/demo"
foreach ($event in @("PreToolUse", "UserPromptSubmit", "SessionStart")) {
    if (-not $codexHooks.Contains($event)) {
        $failures += "Codex translation dropped event '$event' which is in its own documented vocabulary"
    }
}
if ($codexHooks.Contains("NotAnEvent")) {
    $failures += "Codex translation must drop events not in Codex's documented vocabulary"
}
if ($codexHooks["PreToolUse"][0].hooks[0].command -ne 'python3 "/plugins/demo/hooks/pre.py"') {
    $failures += "Codex translation must rewrite `${CLAUDE_PLUGIN_ROOT} to the resolved absolute plugin dir, got: $($codexHooks['PreToolUse'][0].hooks[0].command)"
}
$sessionStartHook = $codexHooks["SessionStart"][0].hooks[0]
if ($sessionStartHook.PSObject.Properties['shell'] -or $sessionStartHook.PSObject.Properties['async']) {
    $failures += "Codex translation must drop fields (shell, async) that aren't in Codex's documented hook schema"
}
if ($codexHooks["SessionStart"][0].matcher -ne "startup") {
    $failures += "Codex translation must preserve the matcher field"
}

# --- Antigravity: only PreToolUse/PostToolUse/Stop overlap ---
$antigravityJson = ConvertTo-AntigravityHooksJson -HooksJsonPath (Join-Path $scratch "hooks.json") -PluginDir "/plugins/demo" -PluginName "demo-plugin"
$antigravityObj = $antigravityJson | ConvertFrom-Json
if (-not $antigravityObj.'demo-plugin'.PSObject.Properties['PreToolUse']) {
    $failures += "Antigravity translation should keep PreToolUse"
}
if ($antigravityObj.'demo-plugin'.PSObject.Properties['UserPromptSubmit'] -or $antigravityObj.'demo-plugin'.PSObject.Properties['SessionStart']) {
    $failures += "Antigravity translation must not invent UserPromptSubmit/SessionStart support it doesn't document"
}

# --- A hooks.json with only non-overlapping Antigravity events produces no output ---
@'
{ "hooks": { "UserPromptSubmit": [{ "hooks": [{ "type": "command", "command": "echo hi" }] }] } }
'@ | Set-Content -Path (Join-Path $scratch "only-unsupported.json")
$emptyResult = ConvertTo-AntigravityHooksJson -HooksJsonPath (Join-Path $scratch "only-unsupported.json") -PluginDir "/plugins/demo" -PluginName "demo-plugin"
if ($emptyResult -ne "") {
    $failures += "Expected no output when nothing overlaps Antigravity's event vocabulary, got: $emptyResult"
}

# --- Merge-CodexHooksJson: multiple plugins' arrays for the same event are unioned ---
$pluginAHooks = (ConvertFrom-Json '{"PreToolUse":[{"hooks":[{"type":"command","command":"a"}]}]}')
$pluginBHooks = (ConvertFrom-Json '{"PreToolUse":[{"hooks":[{"type":"command","command":"b"}]}],"Stop":[{"hooks":[{"type":"command","command":"c"}]}]}')
$hooksByPlugin = @{
    "plugin-a" = @{ PreToolUse = @($pluginAHooks.PreToolUse) }
    "plugin-b" = @{ PreToolUse = @($pluginBHooks.PreToolUse); Stop = @($pluginBHooks.Stop) }
}
$mergedPath = Join-Path $scratch "merged-hooks.json"
Merge-CodexHooksJson -HooksConfigPath $mergedPath -HooksByPlugin $hooksByPlugin
$mergedObj = Get-Content $mergedPath -Raw | ConvertFrom-Json
if (@($mergedObj.hooks.PreToolUse).Count -ne 2) {
    $failures += "Expected Merge-CodexHooksJson to union both plugins' PreToolUse arrays (2 entries), got $(@($mergedObj.hooks.PreToolUse).Count)"
}
if ($mergedObj.hooks.Stop[0].hooks[0].command -ne "c") {
    $failures += "Expected Merge-CodexHooksJson to include plugin-b's Stop hook"
}

Remove-Item -Recurse -Force $scratch

if ($failures.Count -gt 0) {
    Write-Output "FAIL: hook translation (Codex JSON / Antigravity JSON / merge)"
    $failures | ForEach-Object { Write-Output "  - $_" }
    exit 1
}

Write-Output "PASS: hook translation — event filtering per target, path rewriting, field dropping, cross-plugin merge"
exit 0
