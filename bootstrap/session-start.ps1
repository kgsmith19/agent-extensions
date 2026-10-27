#!/usr/bin/env pwsh
# Provider-neutral session start, PowerShell twin of session-start.sh (#72).
# Any harness on Windows or pwsh calls this at session start; harness
# adapters stay thin.
#
#   session-start.ps1 [-EnvFile FILE]
#
# 1. provision  — run the agent-extensions installer only when the capability
#                 layer is absent (~/.agents/skills empty or ~/bin/<ae> missing)
# 2. bundle     — when the INFISICAL_* machine identity is in the environment,
#                 materialize the AGENT_* instruction bundle (`ae bundle`,
#                 issue #60): idempotent, prints key names only, never values
# 3. path       — append `export PATH="$HOME/bin:$PATH"` to FILE once, so the
#                 harness exports it for the rest of the session
# 4. queue      — print the owner's execution queue: active item, next item
#                 and handoff (agent_extensions/queue_cli.py brief)
#
# Every step reports ok / present / skipped / degraded and the script always
# exits 0: a missing network or tool degrades the session, never blocks it.
# Overrides (used by tests), run as PowerShell commands:
# AE_SESSION_PROVISION_CMD, AE_SESSION_BUNDLE_CMD, AE_QUEUE_CMD; AE_QUEUE_BASE_URL as in the sh script.
param([string]$EnvFile = "")
$ErrorActionPreference = "Continue"
$RepoRoot = Split-Path -Parent $PSScriptRoot
$Python = if (Get-Command python3 -ErrorAction SilentlyContinue) { "python3" } else { "python" }

function Invoke-Override([string]$Command) {
    # Run an override command in a child pwsh; return its output and exit code.
    $out = & pwsh -NoProfile -NonInteractive -Command $Command 2>&1 | Out-String
    return @{ Out = $out; Code = $LASTEXITCODE }
}

function Get-LastLine([string]$Text) {
    $lines = @($Text -split "`r?`n" | Where-Object { $_.Trim() })
    if ($lines.Count -eq 0) { return "no output" }
    return $lines[-1]
}

# 1. provision ---------------------------------------------------------------
$name = if ($env:AE_COMMAND) { $env:AE_COMMAND } else { "ae" }
$skills = Join-Path $HOME ".agents/skills"
$hasSkills = (Test-Path $skills) -and @(Get-ChildItem -Force $skills -ErrorAction SilentlyContinue).Count -gt 0
$launcher = @("$name", "$name.ps1", "$name.cmd") | ForEach-Object { Join-Path $HOME "bin/$_" } | Where-Object { Test-Path $_ } | Select-Object -First 1
if ($hasSkills -and $launcher) {
    Write-Output "provision: present (~/.agents/skills and $launcher)"
} else {
    if ($env:AE_SESSION_PROVISION_CMD) {
        $r = Invoke-Override $env:AE_SESSION_PROVISION_CMD
    } else {
        $env:PYTHONPATH = $RepoRoot + $(if ($env:PYTHONPATH) { [IO.Path]::PathSeparator + $env:PYTHONPATH })
        $out = & $Python -m agent_extensions.install bootstrap 2>&1 | Out-String
        $r = @{ Out = $out; Code = $LASTEXITCODE }
    }
    if ($r.Code -eq 0) {
        Write-Output "provision: ok"
    } else {
        Write-Output "provision: degraded (installer exited $($r.Code); last line: $(Get-LastLine $r.Out))"
    }
}

# 2. bundle ------------------------------------------------------------------
if ($env:INFISICAL_CLIENT_ID -and $env:INFISICAL_CLIENT_SECRET -and $env:INFISICAL_PROJECT_ID) {
    if ($env:AE_SESSION_BUNDLE_CMD) {
        $r = Invoke-Override $env:AE_SESSION_BUNDLE_CMD
    } else {
        $env:PYTHONPATH = $RepoRoot + $(if ($env:PYTHONPATH) { [IO.Path]::PathSeparator + $env:PYTHONPATH })
        $out = & $Python -m agent_extensions.install bundle 2>&1 | Out-String
        $r = @{ Out = $out; Code = $LASTEXITCODE }
    }
    if ($r.Code -eq 0) {
        Write-Output "bundle: ok ($(Get-LastLine $r.Out))"
    } else {
        Write-Output "bundle: degraded (exit $($r.Code): $(Get-LastLine $r.Out))"
    }
} else {
    Write-Output "bundle: skipped (no INFISICAL_* identity in the environment)"
}

# 3. path --------------------------------------------------------------------
$pathLine = 'export PATH="$HOME/bin:$PATH"'
if (-not $EnvFile) {
    Write-Output "path: skipped (no -EnvFile)"
} elseif ((Test-Path $EnvFile) -and ((Get-Content $EnvFile) -contains $pathLine)) {
    Write-Output "path: ok (already exported)"
} else {
    try {
        Add-Content -Path $EnvFile -Value $pathLine -ErrorAction Stop
        Write-Output "path: ok (~/bin exported for this session)"
    } catch {
        Write-Output "path: degraded (cannot write $EnvFile)"
    }
}

# 4. queue -------------------------------------------------------------------
Write-Output "== Execution queue (binding order) =="
if ($env:AE_QUEUE_CMD) {
    $r = Invoke-Override $env:AE_QUEUE_CMD
} else {
    $out = & $Python -I (Join-Path $RepoRoot "agent_extensions/queue_cli.py") brief 2>&1 | Out-String
    $r = @{ Out = $out; Code = $LASTEXITCODE }
}
if ($r.Code -eq 0 -or $r.Code -eq 3) {
    # 3 = QUEUE EMPTY (work_queue.py contract)
    Write-Output $r.Out.TrimEnd()
    Write-Output "Resume with: python3 tools/work_queue.py next (agent-engineering-standard)"
} else {
    Write-Output "queue: degraded (exit $($r.Code): $(Get-LastLine $r.Out))"
}
exit 0
