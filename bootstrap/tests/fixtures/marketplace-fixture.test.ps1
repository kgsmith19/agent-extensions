$ErrorActionPreference = "Stop"
. "$PSScriptRoot\marketplace-fixture.ps1"

$failures = @()
$scratch = Join-Path ([IO.Path]::GetTempPath()) ("ae-fix-" + [Guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Path $scratch -Force | Out-Null

try {
    $repoDir = Join-Path $scratch "plugin-repo"
    $repoSha = New-FixturePluginRepo -DestDir $repoDir
    $mpDir = Join-Path $scratch "marketplace"
    $mpSha = New-FixtureMarketplace -DestDir $mpDir -PluginRepoDir $repoDir -PluginRepoSha $repoSha

    if ($repoSha -notmatch '^[0-9a-f]{40}$') { $failures += "plugin-repo sha malformed" }
    if ($mpSha -notmatch '^[0-9a-f]{40}$') { $failures += "marketplace sha malformed" }

    $manifestPath = Join-Path $mpDir ".claude-plugin\marketplace.json"
    if (-not (Test-Path $manifestPath)) {
        $failures += "fixture has no .claude-plugin/marketplace.json"
    } else {
        $manifest = Get-Content $manifestPath -Raw | ConvertFrom-Json
        $byName = @{}
        foreach ($p in $manifest.plugins) { $byName[$p.name] = $p }

        foreach ($n in @("alpha-skills","beta-mcp-stdio","gamma-mcp-http","delta-malformed","epsilon-invalid-json","zeta-repo-pinned","eta-repo-subpath","theta-repo-unpinned")) {
            if (-not $byName.ContainsKey($n)) { $failures += "manifest missing plugin '$n'" }
        }
        if ($byName.ContainsKey("omega-absent")) { $failures += "'omega-absent' must NOT be in the manifest (negative case)" }

        if ($byName["alpha-skills"].source -ne "./plugins/alpha-skills") { $failures += "alpha-skills source wrong" }
        if ($byName["gamma-mcp-http"].source -ne "./external_plugins/gamma-mcp-http") { $failures += "gamma-mcp-http must live under external_plugins/" }
        if ($byName["zeta-repo-pinned"].source.source -ne "url") { $failures += "zeta must be a url source" }
        if ($byName["zeta-repo-pinned"].source.sha -ne $repoSha) { $failures += "zeta sha must match the plugin repo sha" }
        if ($byName["eta-repo-subpath"].source.path -ne "nested/eta") { $failures += "eta must declare path 'nested/eta'" }
        if ($byName["theta-repo-unpinned"].source.PSObject.Properties['sha']) { $failures += "theta must declare NO sha (unpinned case)" }
    }

    if (-not (Test-Path (Join-Path $mpDir "plugins\alpha-skills\skills\greet\SKILL.md"))) { $failures += "alpha-skills content not at plugins/alpha-skills" }
    if (-not (Test-Path (Join-Path $mpDir "external_plugins\gamma-mcp-http\.mcp.json"))) { $failures += "gamma content not at external_plugins/gamma-mcp-http" }
    if (-not (Test-Path (Join-Path $repoDir "skills\remote-greet\SKILL.md"))) { $failures += "plugin repo missing root skill" }
    if (-not (Test-Path (Join-Path $repoDir "nested\eta\skills\eta-greet\SKILL.md"))) { $failures += "plugin repo missing nested/eta skill" }

    Push-Location $repoDir
    try { $allowAny = (& git config --get uploadpack.allowAnySHA1InWant | Out-String).Trim() } finally { Pop-Location }
    if ($allowAny -ne "true") { $failures += "plugin repo must set uploadpack.allowAnySHA1InWant=true" }
} finally {
    Remove-Item $scratch -Recurse -Force -ErrorAction SilentlyContinue
}

if ($failures.Count -gt 0) {
    Write-Output "FAIL: marketplace fixture"
    $failures | ForEach-Object { Write-Output "  - $_" }
    exit 1
}
Write-Output "PASS: fixture builds real marketplace layout with manifest and all source kinds"
