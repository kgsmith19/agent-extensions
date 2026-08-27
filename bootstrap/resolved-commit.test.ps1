#!/usr/bin/env pwsh
$ErrorActionPreference = "Stop"
. "$PSScriptRoot\sync.ps1" -Import

$failures = @()
$scratch = Join-Path ([IO.Path]::GetTempPath()) ("ae-pin-" + [Guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Path (Join-Path $scratch "bootstrap") -Force | Out-Null
$declPath = Join-Path $scratch "bootstrap\external-marketplaces.json"

$seed = @'
{
  "marketplaces": [
    { "name": "mk-one", "repo": "o/one", "pinnedCommit": "aaa", "plugins": ["p-one", "p-two"] },
    { "name": "mk-two", "repo": "o/two", "pinnedCommit": "bbb", "plugins": ["p-three"] }
  ]
}
'@

try {
    Set-Content -Path $declPath -Value $seed
    $sha = "1111111111111111111111111111111111111111"

    $err = Save-ResolvedCommit -RepoRoot $scratch -MarketplaceName "mk-one" -PluginName "p-two" -Sha $sha
    if ($err -ne "") { $failures += "unexpected error: '$err'" }

    $after = Get-Content $declPath -Raw | ConvertFrom-Json
    $one = @($after.marketplaces) | Where-Object { $_.name -eq "mk-one" }
    $two = @($after.marketplaces) | Where-Object { $_.name -eq "mk-two" }

    $pTwo = @($one.plugins) | Where-Object { $_ -isnot [string] -and $_.name -eq "p-two" }
    if (-not $pTwo) { $failures += "p-two was not converted to an object entry" }
    elseif ($pTwo.resolvedCommit -ne $sha) { $failures += "p-two resolvedCommit wrong: '$($pTwo.resolvedCommit)'" }

    $pOne = @($one.plugins) | Where-Object { $_ -is [string] -and $_ -eq "p-one" }
    if (-not $pOne) { $failures += "p-one should remain an untouched string entry" }

    if (@($one.plugins).Count -ne 2) { $failures += "mk-one should still declare exactly 2 plugins" }
    if (@($two.plugins).Count -ne 1 -or @($two.plugins)[0] -ne "p-three") { $failures += "mk-two must be untouched" }
    if ($one.pinnedCommit -ne "aaa") { $failures += "marketplace pinnedCommit must be preserved" }

    $sha2 = "2222222222222222222222222222222222222222"
    $err = Save-ResolvedCommit -RepoRoot $scratch -MarketplaceName "mk-one" -PluginName "p-two" -Sha $sha2
    if ($err -ne "") { $failures += "second save errored: '$err'" }
    $after2 = Get-Content $declPath -Raw | ConvertFrom-Json
    $one2 = @($after2.marketplaces) | Where-Object { $_.name -eq "mk-one" }
    if (@($one2.plugins).Count -ne 2) { $failures += "second save duplicated an entry" }
    $pTwo2 = @($one2.plugins) | Where-Object { $_ -isnot [string] -and $_.name -eq "p-two" }
    if ($pTwo2.resolvedCommit -ne $sha2) { $failures += "second save did not replace the sha" }

    $err = Save-ResolvedCommit -RepoRoot $scratch -MarketplaceName "nope" -PluginName "p-two" -Sha $sha
    if ($err -eq "") { $failures += "unknown marketplace should be an error" }
    $err = Save-ResolvedCommit -RepoRoot $scratch -MarketplaceName "mk-one" -PluginName "nope" -Sha $sha
    if ($err -eq "") { $failures += "unknown plugin should be an error" }
} finally {
    Remove-Item $scratch -Recurse -Force -ErrorAction SilentlyContinue
}

if ($failures.Count -gt 0) {
    Write-Output "FAIL: resolved-commit persistence"
    $failures | ForEach-Object { Write-Output "  - $_" }
    exit 1
}

Write-Output "PASS: Save-ResolvedCommit pins one plugin, preserves the rest, replaces on update, errors on unknown names"
