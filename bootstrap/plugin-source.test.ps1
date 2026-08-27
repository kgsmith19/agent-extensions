$ErrorActionPreference = "Stop"
. "$PSScriptRoot\sync.ps1" -Import
. "$PSScriptRoot\tests\fixtures\marketplace-fixture.ps1"

$failures = @()
$scratch = Join-Path ([IO.Path]::GetTempPath()) ("ae-src-" + [Guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Path $scratch -Force | Out-Null

try {
    $repoDir = Join-Path $scratch "plugin-repo"
    $repoSha = New-FixturePluginRepo -DestDir $repoDir
    $mpDir = Join-Path $scratch "marketplace"
    New-FixtureMarketplace -DestDir $mpDir -PluginRepoDir $repoDir -PluginRepoSha $repoSha | Out-Null

    # Get-DeclaredPlugins: plain string entries
    $mpStrings = [PSCustomObject]@{ name = "m"; plugins = @("a","b") }
    $got = @(Get-DeclaredPlugins -Marketplace $mpStrings)
    if ($got.Count -ne 2)              { $failures += "string form: expected 2 plugins, got $($got.Count)" }
    if ($got[0].Name -ne "a")          { $failures += "string form: expected Name 'a', got '$($got[0].Name)'" }
    if ($got[0].ResolvedCommit -ne "") { $failures += "string form: ResolvedCommit should be empty" }

    # Get-DeclaredPlugins: object entries, and a mixed array
    $mpObjects = [PSCustomObject]@{ name = "m"; plugins = @(
        [PSCustomObject]@{ name = "c"; resolvedCommit = "abc123" },
        "d"
    ) }
    $got2 = @(Get-DeclaredPlugins -Marketplace $mpObjects)
    if ($got2[0].Name -ne "c")                { $failures += "object form: expected Name 'c'" }
    if ($got2[0].ResolvedCommit -ne "abc123") { $failures += "object form: expected ResolvedCommit 'abc123'" }
    if ($got2[1].Name -ne "d")                { $failures += "mixed form: expected Name 'd'" }
    if ($got2[1].ResolvedCommit -ne "")       { $failures += "mixed form: 'd' ResolvedCommit should be empty" }

    # inline under plugins/
    $s = Get-PluginSource -MarketplaceDir $mpDir -PluginName "alpha-skills"
    if ($s.Kind -ne "inline")                 { $failures += "alpha: expected Kind inline, got '$($s.Kind)'" }
    if ($s.Path -ne "./plugins/alpha-skills") { $failures += "alpha: wrong Path '$($s.Path)'" }

    # inline under external_plugins/
    $s = Get-PluginSource -MarketplaceDir $mpDir -PluginName "gamma-mcp-http"
    if ($s.Kind -ne "inline")                            { $failures += "gamma: expected Kind inline" }
    if ($s.Path -ne "./external_plugins/gamma-mcp-http") { $failures += "gamma: wrong Path '$($s.Path)'" }

    # external repo pinned by sha
    $s = Get-PluginSource -MarketplaceDir $mpDir -PluginName "zeta-repo-pinned"
    if ($s.Kind -ne "repo")           { $failures += "zeta: expected Kind repo, got '$($s.Kind)'" }
    if ($s.Sha -ne $repoSha)          { $failures += "zeta: expected Sha '$repoSha', got '$($s.Sha)'" }
    if ($s.SubPath -ne "")            { $failures += "zeta: SubPath should be empty" }
    if ($s.Url -notmatch '^file:///') { $failures += "zeta: expected file:/// Url, got '$($s.Url)'" }

    # external repo with a subdirectory
    $s = Get-PluginSource -MarketplaceDir $mpDir -PluginName "eta-repo-subpath"
    if ($s.Kind -ne "repo")          { $failures += "eta: expected Kind repo" }
    if ($s.SubPath -ne "nested/eta") { $failures += "eta: expected SubPath 'nested/eta', got '$($s.SubPath)'" }

    # external repo, unpinned
    $s = Get-PluginSource -MarketplaceDir $mpDir -PluginName "theta-repo-unpinned"
    if ($s.Kind -ne "repo") { $failures += "theta: expected Kind repo" }
    if ($s.Sha -ne "")      { $failures += "theta: Sha should be empty, got '$($s.Sha)'" }
    if ($s.Ref -ne "")      { $failures += "theta: Ref should be empty, got '$($s.Ref)'" }

    # declared by us but absent from the manifest
    $s = Get-PluginSource -MarketplaceDir $mpDir -PluginName "omega-absent"
    if ($s.Kind -ne "missing")             { $failures += "omega: expected Kind missing, got '$($s.Kind)'" }
    if ($s.Error -notmatch "omega-absent") { $failures += "omega: Error must name the plugin, got '$($s.Error)'" }

    # marketplace clone with no manifest at all
    $bare = Join-Path $scratch "bare"
    New-Item -ItemType Directory -Path $bare -Force | Out-Null
    $s = Get-PluginSource -MarketplaceDir $bare -PluginName "anything"
    if ($s.Kind -ne "missing")                 { $failures += "bare: expected Kind missing" }
    if ($s.Error -notmatch "marketplace.json") { $failures += "bare: Error must name marketplace.json" }
} finally {
    Remove-Item $scratch -Recurse -Force -ErrorAction SilentlyContinue
}

if ($failures.Count -gt 0) {
    Write-Output "FAIL: plugin source resolution"
    $failures | ForEach-Object { Write-Output "  - $_" }
    exit 1
}
Write-Output "PASS: Get-DeclaredPlugins and Get-PluginSource handle all source kinds and both missing cases"
