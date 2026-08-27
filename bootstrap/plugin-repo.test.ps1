#!/usr/bin/env pwsh
$ErrorActionPreference = "Stop"
. "$PSScriptRoot\sync.ps1" -Import
. "$PSScriptRoot\tests\fixtures\marketplace-fixture.ps1"

$failures = @()
$scratch = Join-Path ([IO.Path]::GetTempPath()) ("ae-repo-" + [Guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Path $scratch -Force | Out-Null

try {
    $repoDir = Join-Path $scratch "plugin-repo"
    $repoSha = New-FixturePluginRepo -DestDir $repoDir
    $repoRef = (& git -C $repoDir symbolic-ref --short HEAD | Out-String).Trim()
    $mpDir = Join-Path $scratch "marketplace"
    New-FixtureMarketplace -DestDir $mpDir -PluginRepoDir $repoDir -PluginRepoSha $repoSha | Out-Null
    $reposDir = Join-Path $scratch "_plugins"

    # pinned by manifest sha
    $src = Get-PluginSource -MarketplaceDir $mpDir -PluginName "zeta-repo-pinned"
    $r = Sync-PluginRepo -PluginReposDir $reposDir -MarketplaceName "fx" -PluginName "zeta-repo-pinned" -Source $src -PinnedCommit ""
    if ($r.Error -ne "")             { $failures += "zeta: unexpected error '$($r.Error)'" }
    if ($r.ResolvedSha -ne $repoSha) { $failures += "zeta: expected sha '$repoSha', got '$($r.ResolvedSha)'" }
    if (-not (Test-Path (Join-Path $r.Dir "skills\remote-greet\SKILL.md"))) { $failures += "zeta: root skill not present in clone" }

    # subpath
    $src = Get-PluginSource -MarketplaceDir $mpDir -PluginName "eta-repo-subpath"
    $r = Sync-PluginRepo -PluginReposDir $reposDir -MarketplaceName "fx" -PluginName "eta-repo-subpath" -Source $src -PinnedCommit ""
    if ($r.Error -ne "") { $failures += "eta: unexpected error '$($r.Error)'" }
    if (-not (Test-Path (Join-Path $r.Dir "skills\eta-greet\SKILL.md"))) { $failures += "eta: Dir must point at the nested/eta subdirectory" }
    if ($r.Dir -notmatch 'eta$') { $failures += "eta: Dir should end at the subpath, got '$($r.Dir)'" }

    # unpinned resolves to HEAD and reports the sha it landed on
    $src = Get-PluginSource -MarketplaceDir $mpDir -PluginName "theta-repo-unpinned"
    $r = Sync-PluginRepo -PluginReposDir $reposDir -MarketplaceName "fx" -PluginName "theta-repo-unpinned" -Source $src -PinnedCommit ""
    if ($r.Error -ne "")             { $failures += "theta: unexpected error '$($r.Error)'" }
    if ($r.ResolvedSha -ne $repoSha) { $failures += "theta: expected HEAD sha '$repoSha', got '$($r.ResolvedSha)'" }

    # idempotent: second call on an already-correct clone succeeds
    $r2 = Sync-PluginRepo -PluginReposDir $reposDir -MarketplaceName "fx" -PluginName "zeta-repo-pinned" -Source (Get-PluginSource -MarketplaceDir $mpDir -PluginName "zeta-repo-pinned") -PinnedCommit ""
    if ($r2.Error -ne "")             { $failures += "zeta rerun: unexpected error '$($r2.Error)'" }
    if ($r2.ResolvedSha -ne $repoSha) { $failures += "zeta rerun: sha changed" }

    # ref-based source is idempotent when the clone already sits at the ref's commit
    $refSrc = [PSCustomObject]@{ Kind = "repo"; Path = ""; Url = $src.Url; Sha = ""; Ref = $repoRef; SubPath = ""; Error = "" }
    $refPlugin = "theta-repo-ref"
    $ref1 = Sync-PluginRepo -PluginReposDir $reposDir -MarketplaceName "fx" -PluginName $refPlugin -Source $refSrc -PinnedCommit ""
    if ($ref1.Error -ne "")                { $failures += "theta ref: unexpected error '$($ref1.Error)'" }
    if ($ref1.ResolvedSha -ne $repoSha)    { $failures += "theta ref: expected ref sha '$repoSha', got '$($ref1.ResolvedSha)'" }
    $sentinel = Join-Path $ref1.Dir "sentinel.txt"
    Set-Content -Path $sentinel -Value "keep"
    $ref2 = Sync-PluginRepo -PluginReposDir $reposDir -MarketplaceName "fx" -PluginName $refPlugin -Source $refSrc -PinnedCommit ""
    if ($ref2.Error -ne "")                { $failures += "theta ref rerun: unexpected error '$($ref2.Error)'" }
    if ($ref2.ResolvedSha -ne $repoSha)    { $failures += "theta ref rerun: sha changed" }
    if (-not (Test-Path $sentinel))        { $failures += "theta ref rerun: expected existing clone to remain in place" }

    # PinnedCommit wins over the manifest sha
    $r3 = Sync-PluginRepo -PluginReposDir $reposDir -MarketplaceName "fx" -PluginName "theta-repo-unpinned" -Source $src -PinnedCommit $repoSha
    if ($r3.Error -ne "")             { $failures += "theta pinned: unexpected error '$($r3.Error)'" }
    if ($r3.ResolvedSha -ne $repoSha) { $failures += "theta pinned: expected '$repoSha'" }

    # unreachable commit is a reported error, never a silent success
    $bogus = "0123456789012345678901234567890123456789"
    $badSrc = [PSCustomObject]@{ Kind = "repo"; Path = ""; Url = $src.Url; Sha = $bogus; Ref = ""; SubPath = ""; Error = "" }
    $r4 = Sync-PluginRepo -PluginReposDir $reposDir -MarketplaceName "fx" -PluginName "bad-sha" -Source $badSrc -PinnedCommit ""
    if ($r4.Error -eq "")                { $failures += "bad-sha: expected a reported error, got none" }
    if ($r4.Dir -ne "")                  { $failures += "bad-sha: Dir must be empty when Error is set" }
    if ($r4.Error -notmatch $bogus)      { $failures += "bad-sha: error must name the unreachable commit" }

    # missing subpath is a reported error
    $subSrc = [PSCustomObject]@{ Kind = "repo"; Path = ""; Url = $src.Url; Sha = $repoSha; Ref = ""; SubPath = "no/such/dir"; Error = "" }
    $r5 = Sync-PluginRepo -PluginReposDir $reposDir -MarketplaceName "fx" -PluginName "bad-subpath" -Source $subSrc -PinnedCommit ""
    if ($r5.Error -eq "")                  { $failures += "bad-subpath: expected a reported error" }
    if ($r5.Error -notmatch "no/such/dir") { $failures += "bad-subpath: error must name the missing subdirectory" }
} finally {
    Remove-Item $scratch -Recurse -Force -ErrorAction SilentlyContinue
}

if ($failures.Count -gt 0) {
    Write-Output "FAIL: external plugin repo sync"
    $failures | ForEach-Object { Write-Output "  - $_" }
    exit 1
}
Write-Output "PASS: Sync-PluginRepo handles sha, ref, subpath, unpinned HEAD, idempotency, and reports unreachable commits"
