#!/usr/bin/env pwsh
# One command: declared roster against actual state on every provider and
# surface. Runs every check, aggregates failures, and exits non-zero naming
# every difference — never stopping at the first failure, so one broken
# surface doesn't hide problems on another.
param(
    [string]$RepoRoot = (Split-Path -Parent $PSScriptRoot)
)

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "sync.ps1") -Import

$script:checksOk = @()
$script:checksFailed = @()
$script:overallFailed = $false

function Invoke-Check {
    param([string]$CheckName, [scriptblock]$Fn)
    Write-Output "--- $CheckName ---"
    # Pass/fail is tracked via this side-effect variable, not a captured
    # return value: assigning a scriptblock's invocation (`$x = & $Fn`)
    # bundles EVERY line the block prints — including a native `pwsh -File`
    # subprocess's own stdout — into that one collection, which is always
    # truthy if non-empty regardless of what actually failed. Calling `&
    # $Fn` as a bare statement instead lets that output print normally.
    $script:lastCheckPassed = $true
    try {
        & $Fn
    } catch {
        Write-Output $_.Exception.Message
        $script:lastCheckPassed = $false
    }
    Write-Output ""
    if ($script:lastCheckPassed) {
        $script:checksOk += $CheckName
    } else {
        $script:checksFailed += $CheckName
        $script:overallFailed = $true
    }
}

Invoke-Check -CheckName "content" -Fn {
    & pwsh -NoProfile -File (Join-Path $PSScriptRoot "verify-content.ps1") -RepoRoot $RepoRoot
    if ($LASTEXITCODE -ne 0) { $script:lastCheckPassed = $false }
}

Invoke-Check -CheckName "pointers" -Fn {
    & pwsh -NoProfile -File (Join-Path $PSScriptRoot "verify-pointers.ps1") -RepoRoot $RepoRoot
    if ($LASTEXITCODE -ne 0) { $script:lastCheckPassed = $false }
}

Invoke-Check -CheckName "external" -Fn {
    & pwsh -NoProfile -File (Join-Path $PSScriptRoot "verify-external.ps1") -RepoRoot $RepoRoot
    if ($LASTEXITCODE -ne 0) { $script:lastCheckPassed = $false }
}

Invoke-Check -CheckName "account" -Fn {
    $manifestPath = Join-Path $RepoRoot "bootstrap\account-manifest.json"
    if (-not (Test-Path $manifestPath)) {
        Write-Output "Missing $manifestPath"
        $script:lastCheckPassed = $false
        return
    }
    try {
        $manifest = Get-Content $manifestPath -Raw | ConvertFrom-Json
    } catch {
        Write-Output "$manifestPath is not valid JSON"
        $script:lastCheckPassed = $false
        return
    }
    foreach ($skill in @($manifest.skills)) {
        $sourcePath = Join-Path $RepoRoot $skill.source
        if (-not (Test-Path $sourcePath)) {
            Write-Output "Account skill '$($skill.name)': declared source '$($skill.source)' does not exist"
            $script:lastCheckPassed = $false
        }
    }
    # A pending ADD/REMOVE checklist is expected, routine state (Kyle
    # hasn't uploaded the latest change by hand yet) — reported for
    # visibility, but not itself a verification failure the way a broken
    # source path is.
    Write-AccountChecklist -RepoRoot $RepoRoot
}

Invoke-Check -CheckName "translations" -Fn {
    $vendorCacheDir = Join-Path $RepoRoot ".vendor-cache"
    $reportPath = Join-Path $RepoRoot "bootstrap\command-gap-report.md"
    $before = if (Test-Path $reportPath) { Get-Content $reportPath -Raw } else { "" }
    New-CommandGapReport -RepoRoot $RepoRoot -VendorCacheDir $vendorCacheDir
    $after = Get-Content $reportPath -Raw
    if ($before -ne $after) {
        Write-Output "bootstrap/command-gap-report.md was stale relative to the declared roster — regenerated, review and commit the diff"
        $script:lastCheckPassed = $false
    } else {
        Write-Output "command-gap-report.md matches the declared roster"
    }

    # Every mechanically-translatable agent should have produced a file on
    # each target — catches one being deleted or never generated
    # out-of-band, which a clean sync exit code alone would not reveal.
    $expected = 0
    foreach ($mp in (Get-ExternalMarketplaces -RepoRoot $RepoRoot)) {
        foreach ($declared in (Get-DeclaredPlugins -Marketplace $mp)) {
            $pluginName = $declared.Name
            $resolved = Resolve-PluginDir -RepoRoot $RepoRoot -VendorCacheDir $vendorCacheDir -Marketplace $mp -DeclaredPlugin $declared
            if ($resolved.Failure -ne "") { continue }
            $agentsRoot = Join-Path $resolved.Dir "agents"
            if (-not (Test-Path $agentsRoot)) { continue }
            foreach ($agentFile in (Get-ChildItem $agentsRoot -Filter "*.md" -File -ErrorAction SilentlyContinue)) {
                $parsed = Get-AgentFrontmatter -AgentMdPath $agentFile.FullName
                if ($parsed.ErrorText -ne "") { continue }
                $expected++
                $base = "$pluginName-$([System.IO.Path]::GetFileNameWithoutExtension($agentFile.Name))"
                if (-not (Test-Path (Join-Path $CodexAgentsDir "$base.toml"))) {
                    Write-Output "Codex agent '$base' is missing (source translates cleanly but no file was found)"
                    $script:lastCheckPassed = $false
                }
                if (-not (Test-Path (Join-Path $AntigravityAgentsDir "$base.md"))) {
                    Write-Output "Antigravity agent '$base' is missing (source translates cleanly but no file was found)"
                    $script:lastCheckPassed = $false
                }
            }
        }
    }
    Write-Output "Checked $expected mechanically-translatable agent(s) against both targets"
}

Write-Output "--- verify summary ---"
if ($script:checksOk.Count -gt 0) {
    Write-Output "OK: $($script:checksOk -join ' ')"
}
if ($script:checksFailed.Count -gt 0) {
    Write-Output "FAILED: $($script:checksFailed -join ' ')"
}

if ($script:overallFailed) {
    Write-Output "VERIFY FAILED"
    exit 1
}

Write-Output "VERIFY OK"
exit 0
