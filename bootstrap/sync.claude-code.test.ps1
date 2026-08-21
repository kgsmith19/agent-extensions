#!/usr/bin/env pwsh
# Exercises the real `claude` CLI against this repo. Not scratch-isolated —
# see Task 5's note in the plan for why that isn't possible for this part.
$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $PSScriptRoot
. (Join-Path $PSScriptRoot "sync.ps1") -Import

Sync-ClaudeCodeMarketplace -RepoRoot $RepoRoot

$listOutput = & claude plugin list 2>&1 | Out-String
$failures = @()

if ($listOutput -notmatch "anthropic-product-skills@agent-extensions") {
    $failures += "claude plugin list does not show anthropic-product-skills@agent-extensions"
}
if ($listOutput -notmatch "general-skills@agent-extensions") {
    $failures += "claude plugin list does not show general-skills@agent-extensions"
}

# Idempotency: re-run must not error and must still show both plugins
Sync-ClaudeCodeMarketplace -RepoRoot $RepoRoot
$listOutput2 = & claude plugin list 2>&1 | Out-String
if ($listOutput2 -notmatch "anthropic-product-skills@agent-extensions" -or
    $listOutput2 -notmatch "general-skills@agent-extensions") {
    $failures += "Second sync run lost a plugin registration"
}

# --- External marketplace: register one real, small plugin end-to-end ---
$externalFailures = Sync-ClaudeCodeMarketplace -RepoRoot $RepoRoot
if ($externalFailures.Count -gt 0) {
    $failures += "External marketplace sync reported failures: $($externalFailures -join '; ')"
}

$listOutput3 = & claude plugin list 2>&1 | Out-String
if ($listOutput3 -notmatch "elements-of-style@superpowers-marketplace") {
    $failures += "claude plugin list does not show elements-of-style@superpowers-marketplace after external sync"
}

# --- Idempotency for the external part too ---
Sync-ClaudeCodeMarketplace -RepoRoot $RepoRoot | Out-Null
$listOutput4 = & claude plugin list 2>&1 | Out-String
if ($listOutput4 -notmatch "elements-of-style@superpowers-marketplace") {
    $failures += "Second sync run lost the elements-of-style@superpowers-marketplace registration"
}

if ($failures.Count -gt 0) {
    Write-Output "FAIL: sync.ps1 Claude Code registration"
    $failures | ForEach-Object { Write-Output "  - $_" }
    exit 1
}

Write-Output "PASS: Claude Code marketplace, own plugins, and one external marketplace/plugin registered and idempotent"
exit 0
