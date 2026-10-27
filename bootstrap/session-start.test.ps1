#!/usr/bin/env pwsh
# session-start.ps1 (#72): the PowerShell twin of session-start.sh. It
# provisions only when needed, adds PATH once, prints the queue brief
# (active, next, handoff) and never fails the session. Runs against a
# throwaway HOME with recording stubs, plus a stub of std's queue CLI.
$ErrorActionPreference = "Stop"
$Script = Join-Path $PSScriptRoot "session-start.ps1"
$scratch = Join-Path ([System.IO.Path]::GetTempPath()) "agent-extensions-sstest-$(Get-Random)"
New-Item -ItemType Directory -Path $scratch -Force | Out-Null
$failures = @()

function Reset-Home {
    Remove-Item -Recurse -Force (Join-Path $scratch "home"), (Join-Path $scratch "calls") -ErrorAction SilentlyContinue
    New-Item -ItemType Directory -Path (Join-Path $scratch "home") -Force | Out-Null
    Set-Content -Path (Join-Path $scratch "calls") -Value $null
}

function Invoke-SessionStart([hashtable]$Env, [string[]]$Arguments = @()) {
    # Runs the script in a child pwsh with exactly these overrides set.
    $names = @("HOME", "AE_SESSION_PROVISION_CMD", "AE_QUEUE_CMD", "AE_QUEUE_BASE_URL", "AES_STANDARD_DIR", "AE_COMMAND",
        "AE_SESSION_BUNDLE_CMD", "INFISICAL_CLIENT_ID", "INFISICAL_CLIENT_SECRET", "INFISICAL_PROJECT_ID")
    $saved = @{}
    foreach ($name in $names) { $saved[$name] = [Environment]::GetEnvironmentVariable($name) }
    try {
        foreach ($name in $names) { [Environment]::SetEnvironmentVariable($name, $null) }
        [Environment]::SetEnvironmentVariable("HOME", (Join-Path $scratch "home"))
        foreach ($key in $Env.Keys) { [Environment]::SetEnvironmentVariable($key, $Env[$key]) }
        $out = & pwsh -NoProfile -NonInteractive -File $Script @Arguments 2>&1 | Out-String
        return @{ Out = $out; Code = $LASTEXITCODE }
    } finally {
        foreach ($name in $names) { [Environment]::SetEnvironmentVariable($name, $saved[$name]) }
    }
}

$calls = Join-Path $scratch "calls"
$recordProvision = "Add-Content -Path '$calls' -Value provision"
$queueHead = "Write-Output 'Q07 [pending] next thing'"

# provisions on an empty HOME
Reset-Home
$r = Invoke-SessionStart @{ AE_SESSION_PROVISION_CMD = $recordProvision; AE_QUEUE_CMD = $queueHead }
if (@(Get-Content $calls | Where-Object { $_ -eq "provision" }).Count -ne 1) { $failures += "empty HOME did not provision exactly once" }
if ($r.Out -notmatch "provision: ok") { $failures += "missing 'provision: ok': $($r.Out)" }

# skips when skills and launcher are present
Reset-Home
New-Item -ItemType Directory -Path (Join-Path $scratch "home/.agents/skills/some-skill") -Force | Out-Null
New-Item -ItemType Directory -Path (Join-Path $scratch "home/bin") -Force | Out-Null
Set-Content -Path (Join-Path $scratch "home/bin/ae.ps1") -Value "# launcher"
$r = Invoke-SessionStart @{ AE_SESSION_PROVISION_CMD = $recordProvision; AE_QUEUE_CMD = $queueHead }
if ((Get-Content $calls) -contains "provision") { $failures += "provisioned although skills and launcher were present" }
if ($r.Out -notmatch "provision: present") { $failures += "missing 'provision: present': $($r.Out)" }

# PATH export appended exactly once across runs
Reset-Home
$envFile = Join-Path $scratch "env-path"
Set-Content -Path $envFile -Value $null
1..2 | ForEach-Object { Invoke-SessionStart @{ AE_SESSION_PROVISION_CMD = "exit 0"; AE_QUEUE_CMD = $queueHead } @("-EnvFile", $envFile) | Out-Null }
$n = @(Get-Content $envFile | Where-Object { $_ -eq 'export PATH="$HOME/bin:$PATH"' }).Count
if ($n -ne 1) { $failures += "expected 1 PATH line in env file, found $n" }

# no env file: path skipped
Reset-Home
$r = Invoke-SessionStart @{ AE_SESSION_PROVISION_CMD = "exit 0"; AE_QUEUE_CMD = $queueHead }
if ($r.Out -notmatch "path: skipped") { $failures += "missing 'path: skipped': $($r.Out)" }

# queue head under the fixed header; failing queue and provisioning degrade, exit 0
Reset-Home
$r = Invoke-SessionStart @{ AE_SESSION_PROVISION_CMD = "exit 0"; AE_QUEUE_CMD = $queueHead }
if ($r.Out -notmatch "== Execution queue \(binding order\) ==") { $failures += "queue header missing: $($r.Out)" }
if ($r.Out -notmatch "Q07 \[pending\] next thing") { $failures += "queue head missing: $($r.Out)" }
$r = Invoke-SessionStart @{ AE_SESSION_PROVISION_CMD = "exit 7"; AE_QUEUE_CMD = "Write-Error boom; exit 5" }
if ($r.Code -ne 0) { $failures += "failures changed the exit code to $($r.Code)" }
if ($r.Out -notmatch "provision: degraded") { $failures += "missing 'provision: degraded': $($r.Out)" }
if ($r.Out -notmatch "queue: degraded \(exit 5") { $failures += "missing 'queue: degraded (exit 5': $($r.Out)" }

# default queue command: std's CLI fetched from AE_QUEUE_BASE_URL, brief printed
$stub = Join-Path $scratch "stub"
New-Item -ItemType Directory -Path $stub -Force | Out-Null
@'
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import queue_core, queue_store
args = sys.argv[1:]
if args == ["next"]:
    print("Q99 [active] stub head")
elif args == ["show"]:
    print("Q99 [active] stub head")
elif args == ["handoff", "show"]:
    print("STUB HANDOFF")
'@ | Set-Content -Path (Join-Path $stub "work_queue.py")
"OK = True" | Set-Content -Path (Join-Path $stub "queue_core.py")
"OK = True" | Set-Content -Path (Join-Path $stub "queue_store.py")
Reset-Home
$r = Invoke-SessionStart @{ AE_SESSION_PROVISION_CMD = "exit 0"; AE_QUEUE_BASE_URL = "file://" + $stub.Replace("\", "/") }
foreach ($want in @("Active item:", "Next item:", "Q99 \[active\] stub head", "Handoff:", "STUB HANDOFF", "Resume with:")) {
    if ($r.Out -notmatch $want) { $failures += "brief missing '$want': $($r.Out)" }
}
if ($r.Code -ne 0) { $failures += "brief run exit $($r.Code)" }

# #60: with the machine identity present, the bundle step runs once and reports ok
$recordBundle = "Add-Content -Path '$calls' -Value bundle; Write-Output 'applied  bundle: AGENT_GLOBAL_RULES_MD: up to date'"
$identity = @{ INFISICAL_CLIENT_ID = "cid"; INFISICAL_CLIENT_SECRET = "s3cret-canary"; INFISICAL_PROJECT_ID = "proj" }
Reset-Home
$envs = @{ AE_SESSION_PROVISION_CMD = "exit 0"; AE_QUEUE_CMD = $queueHead; AE_SESSION_BUNDLE_CMD = $recordBundle } + $identity
$r = Invoke-SessionStart $envs
if (@(Get-Content $calls | Where-Object { $_ -eq "bundle" }).Count -ne 1) { $failures += "#60: identity present but the bundle step did not run exactly once" }
if ($r.Out -notmatch "bundle: ok") { $failures += "#60: missing 'bundle: ok': $($r.Out)" }
if ($r.Out -match "s3cret-canary") { $failures += "#60: the client secret leaked into session output" }

# #60: without the identity the bundle step is skipped and never runs
Reset-Home
$r = Invoke-SessionStart @{ AE_SESSION_PROVISION_CMD = "exit 0"; AE_QUEUE_CMD = $queueHead; AE_SESSION_BUNDLE_CMD = $recordBundle }
if ((Get-Content $calls) -contains "bundle") { $failures += "#60: bundle step ran without an identity" }
if ($r.Out -notmatch [regex]::Escape("bundle: skipped (no INFISICAL_* identity in the environment)")) { $failures += "#60: missing 'bundle: skipped': $($r.Out)" }

# #60: a failing bundle step degrades, exit stays 0
Reset-Home
$envs = @{ AE_SESSION_PROVISION_CMD = "exit 0"; AE_QUEUE_CMD = $queueHead; AE_SESSION_BUNDLE_CMD = "Write-Output 'failed   bundle: secret provider returned HTTP 401'; exit 1" } + $identity
$r = Invoke-SessionStart $envs
if ($r.Code -ne 0) { $failures += "#60: failing bundle step changed exit code to $($r.Code)" }
if ($r.Out -notmatch [regex]::Escape("bundle: degraded (exit 1: failed   bundle: secret provider returned HTTP 401)")) { $failures += "#60: missing 'bundle: degraded': $($r.Out)" }

Remove-Item -Recurse -Force $scratch -ErrorAction SilentlyContinue
if ($failures.Count -gt 0) {
    $failures | ForEach-Object { Write-Output "FAIL: $_" }
    exit 1
}
Write-Output "PASS: session-start.ps1 provisions only when needed, adds PATH once, prints the queue brief, never fails the session"
