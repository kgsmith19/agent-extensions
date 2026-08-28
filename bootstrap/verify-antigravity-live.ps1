#!/usr/bin/env pwsh
# One-shot, real-CLI verification that a repo-synced plugin's hook actually
# fires through Antigravity's own hook engine -- not just that hooks.json
# was generated correctly (verify-external.ps1 already proves that from a
# sandbox with no Antigravity installed) and not just that the hook script
# runs correctly when invoked by hand (proven in this repo's own test
# suite, and by direct testing against the real vendored hookify plugin in
# PR #2). This is the one thing nothing else in this repo can prove,
# because it requires a real, authenticated `agy` CLI session tied to your
# own Antigravity account -- headless mode explicitly documents that it
# "uses your cached credentials" from a prior interactive login, with no
# API-key or service-account path, so no cloud sandbox can do this step on
# your behalf. Run this on the machine where `agy` is already installed
# and authenticated (run `agy` once interactively first if you haven't).
#
# This script is designed against agy's documented headless interface
# (`agy -p "<prompt>" --output-format json`, antigravity.google/docs/cli/
# headless) but has NOT been run against a real agy session anywhere in
# this effort -- no environment involved had authenticated credentials.
# If the actual output shape differs from what's matched below, read the
# raw output printed on failure/INFO rather than trusting the verdict
# blindly, exactly the same discipline this whole effort has applied to
# every other report along the way.
#
# What it does:
#   1. Confirms `agy` is on PATH and already authenticated (fails fast
#      with a clear message otherwise, rather than hanging).
#   2. Confirms plain tool execution isn't blocked by an unrelated
#      built-in hook (the googlecloudtools.datacloud_telemetry issue
#      that blocked the whole pipeline earlier in this effort).
#   3. Writes a temporary hookify rule blocking a distinctive marker
#      command.
#   4. Asks agy, via a real headless prompt (not a hand-invoked script,
#      unlike every test run so far), to run that exact command as a
#      real tool call.
#   5. Reports whether it was actually blocked (the hook fired for real)
#      or ran through (it didn't) -- the one open question this whole
#      completion effort has been chasing.
#   6. Cleans up the temporary rule.
#   7. Asks agy to list its available custom agents and checks whether a
#      real translated agent name shows up, resolving the other open
#      question: whether ~/.gemini/config/agents/ (this repo's
#      implemented target) is actually what the CLI reads.

$ErrorActionPreference = "Stop"
$results = [ordered]@{}

function Write-Result($name, $status, $detail) {
    $script:results[$name] = @{ status = $status; detail = $detail }
    $marker = if ($status -eq "PASS") { "[PASS]" } elseif ($status -eq "FAIL") { "[FAIL]" } else { "[INFO]" }
    Write-Output "$marker $name -- $detail"
}

# --- Step 1: agy present and authenticated ---
$agy = Get-Command agy -ErrorAction SilentlyContinue
if (-not $agy) {
    Write-Result "cli-present" "FAIL" "agy not found on PATH. Install per https://antigravity.google/docs/cli/getting-started, then run 'agy' once interactively to authenticate, then re-run this script."
    Write-Output ""; Write-Output "--- summary ---"; Write-Output "cli-present: FAIL"
    exit 1
}
Write-Result "cli-present" "PASS" "agy found at $($agy.Source)"

$authProbe = & agy -p "reply with exactly: PONG" --output-format json 2>&1 | Out-String
if ($LASTEXITCODE -ne 0 -or $authProbe -match "authentication required") {
    Write-Result "cli-authenticated" "FAIL" "agy is not authenticated in this shell. Run 'agy' interactively once, then re-run this script. Raw output: $authProbe"
    Write-Output ""; Write-Output "--- summary ---"; Write-Output "cli-authenticated: FAIL"
    exit 1
}
Write-Result "cli-authenticated" "PASS" "headless probe responded"

# --- Step 2: tool execution isn't blocked by an unrelated built-in hook ---
$toolProbe = & agy -p "Run the shell command: echo AGENT_EXTENSIONS_TOOLCHECK" --output-format json 2>&1 | Out-String
if ($toolProbe -notmatch "AGENT_EXTENSIONS_TOOLCHECK") {
    Write-Result "tool-execution" "FAIL" "A plain, unblocked shell command did not appear to execute -- something (possibly the googlecloudtools.datacloud_telemetry built-in hook seen earlier in this effort) may still be blocking the tool-execution pipeline before any repo-synced hook gets a chance to run. Raw output: $toolProbe"
    Write-Output ""; Write-Output "--- summary ---"
    foreach ($k in $results.Keys) { Write-Output "$k`: $($results[$k].status)" }
    Write-Output "tool-execution: FAIL"
    exit 1
}
Write-Result "tool-execution" "PASS" "a plain shell command executed and its output came back through agy"

# --- Step 3: write a temporary, distinctive hookify rule ---
$ruleDir = ".claude"
New-Item -ItemType Directory -Path $ruleDir -Force | Out-Null
$rulePath = Join-Path $ruleDir "hookify.agent-extensions-live-check.local.md"
@"
---
name: agent-extensions-live-check
enabled: true
event: bash
pattern: AGENT_EXTENSIONS_HOOK_MARKER_87234
action: block
---
Temporary rule written by bootstrap/verify-antigravity-live.ps1 to prove a
repo-synced hook fires through Antigravity's real hook engine. Safe to
delete by hand; this script removes it automatically when done.
"@ | Set-Content -Path $rulePath

try {
    # --- Step 4: ask agy to run the exact command that rule should block, for real ---
    $blockProbe = & agy -p "Run this exact shell command and tell me exactly what happened, including any denial or error message verbatim: echo AGENT_EXTENSIONS_HOOK_MARKER_87234" --output-format json 2>&1 | Out-String

    # --- Step 5: interpret the result ---
    if ($blockProbe -match "AGENT_EXTENSIONS_HOOK_MARKER_87234" -and $blockProbe -notmatch "(?i)(denied|blocked|prevent)") {
        Write-Result "hook-fires-for-real" "FAIL" "The command that should have been blocked by hookify's rule appears to have run anyway. hookify's PreToolUse hook is NOT firing through Antigravity's real hook engine, even though it works correctly when invoked by hand (already proven in PR #2). Raw output: $blockProbe"
    } elseif ($blockProbe -match "(?i)(denied|blocked|prevent)") {
        Write-Result "hook-fires-for-real" "PASS" "The command was blocked -- hookify's PreToolUse hook fired for real through Antigravity's hook engine. This closes the one remaining open question in the whole agent-extensions completion effort."
    } else {
        Write-Result "hook-fires-for-real" "INFO" "Inconclusive -- neither a clear block nor a clear pass-through was detected in the output. Read it directly rather than trusting this script's pattern-matching: $blockProbe"
    }
} finally {
    Remove-Item -Path $rulePath -ErrorAction SilentlyContinue
}

# --- Step 6: are translated agents actually visible to agy? ---
# Resolves the other open question from this effort: whether translated
# agents at ~/.gemini/config/agents/ (this repo's implemented target) are
# what Antigravity's CLI actually reads, or whether it needs a second
# copy under ~/.gemini/antigravity-cli/agents/ instead (a real directory
# on Kyle's machine, seen holding Antigravity's own built-in content, but
# never confirmed as an agent-loading path this repo also needs to write).
$probeAgentDir = Join-Path $HOME ".gemini/config/agents"
$probeAgentFile = Get-ChildItem $probeAgentDir -Filter "*.md" -File -ErrorAction SilentlyContinue | Select-Object -First 1
if ($probeAgentFile) {
    $probeAgentName = [System.IO.Path]::GetFileNameWithoutExtension($probeAgentFile.Name)
    $agentProbe = & agy -p "List the names of your available custom agents or personas, exactly as they would be invoked." --output-format json 2>&1 | Out-String
    if ($agentProbe -match [regex]::Escape($probeAgentName)) {
        Write-Result "agents-visible" "PASS" "Translated agent '$probeAgentName' (from ~/.gemini/config/agents/) is visible to agy -- no second path needed."
    } else {
        Write-Result "agents-visible" "FAIL" "Translated agent '$probeAgentName' was not found in agy's own agent listing. ~/.gemini/config/agents/ may not be what the CLI reads -- check whether it also needs writing to ~/.gemini/antigravity-cli/agents/ (sync.ps1 -AntigravityAgentsDir can redirect there without a code change once confirmed). Raw output: $agentProbe"
    }
} else {
    Write-Result "agents-visible" "INFO" "No translated agent files found at $probeAgentDir to probe with -- run bootstrap/sync.ps1 first."
}

Write-Output ""
Write-Output "--- summary ---"
foreach ($k in $results.Keys) {
    Write-Output "$k`: $($results[$k].status)"
}
if ($results.Values | Where-Object { $_.status -eq "FAIL" }) {
    exit 1
}
exit 0
