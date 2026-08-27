function New-FixtureSkill {
    param([string]$SkillsRoot, [string]$Name, [string]$Description)
    $dir = Join-Path $SkillsRoot $Name
    New-Item -ItemType Directory -Path $dir -Force | Out-Null
    Set-Content -Path (Join-Path $dir "SKILL.md") -Value @"
---
name: $Name
description: $Description
---

Fixture skill body.
"@
}

function New-FixturePluginRepo {
    param([string]$DestDir)

    if (Test-Path $DestDir) { Remove-Item $DestDir -Recurse -Force }
    New-Item -ItemType Directory -Path $DestDir -Force | Out-Null

    New-FixtureSkill -SkillsRoot (Join-Path $DestDir "skills") -Name "remote-greet" -Description "Fixture skill served from an external plugin repo."
    New-FixtureSkill -SkillsRoot (Join-Path $DestDir "nested\eta\skills") -Name "eta-greet" -Description "Fixture skill in a subdirectory of an external plugin repo."

    Push-Location $DestDir
    try {
        & git init -q
        & git config user.email "fixture@agent-extensions.test"
        & git config user.name "agent-extensions fixture"
        & git config uploadpack.allowAnySHA1InWant true
        & git add -A
        & git commit -q -m "Fixture external plugin repo"
        return (& git rev-parse HEAD | Out-String).Trim()
    } finally { Pop-Location }
}

function New-FixtureMarketplace {
    param([string]$DestDir, [string]$PluginRepoDir, [string]$PluginRepoSha)

    if (Test-Path $DestDir) { Remove-Item $DestDir -Recurse -Force }
    New-Item -ItemType Directory -Path $DestDir -Force | Out-Null

    New-FixtureSkill -SkillsRoot (Join-Path $DestDir "plugins\alpha-skills\skills") -Name "greet" -Description "Says hello. Fixture skill for agent-extensions sync tests."

    $beta = Join-Path $DestDir "plugins\beta-mcp-stdio"
    New-Item -ItemType Directory -Path $beta -Force | Out-Null
    Set-Content -Path (Join-Path $beta ".mcp.json") -Value @'
{
  "mcpServers": {
    "fixture-stdio": {
      "command": "node",
      "args": ["server.js", "--port", "0"],
      "env": { "FIXTURE_MODE": "stdio" }
    }
  }
}
'@

    $gamma = Join-Path $DestDir "external_plugins\gamma-mcp-http"
    New-Item -ItemType Directory -Path $gamma -Force | Out-Null
    Set-Content -Path (Join-Path $gamma ".mcp.json") -Value @'
{
  "mcpServers": {
    "fixture-http": {
      "url": "https://fixture.example.com/mcp",
      "headers": { "Authorization": "Bearer FIXTURE_TOKEN" }
    }
  }
}
'@

    $delta = Join-Path $DestDir "plugins\delta-malformed"
    New-Item -ItemType Directory -Path $delta -Force | Out-Null
    Set-Content -Path (Join-Path $delta ".mcp.json") -Value @'
{
  "mcpServers": {
    "fixture-bad": {
      "transportKind": "carrier-pigeon"
    }
  }
}
'@

    $epsilon = Join-Path $DestDir "plugins\epsilon-invalid-json"
    New-Item -ItemType Directory -Path $epsilon -Force | Out-Null
    Set-Content -Path (Join-Path $epsilon ".mcp.json") -Value '{ "mcpServers": { "broken": } }'

    $repoUrl = "file:///" + ($PluginRepoDir -replace '\\','/')
    $manifest = [ordered]@{
        name    = "fixture-marketplace"
        plugins = @(
            [ordered]@{ name = "alpha-skills";         source = "./plugins/alpha-skills" },
            [ordered]@{ name = "beta-mcp-stdio";       source = "./plugins/beta-mcp-stdio" },
            [ordered]@{ name = "gamma-mcp-http";       source = "./external_plugins/gamma-mcp-http" },
            [ordered]@{ name = "delta-malformed";      source = "./plugins/delta-malformed" },
            [ordered]@{ name = "epsilon-invalid-json"; source = "./plugins/epsilon-invalid-json" },
            [ordered]@{ name = "zeta-repo-pinned";     source = [ordered]@{ source = "url"; url = $repoUrl; sha = $PluginRepoSha } },
            [ordered]@{ name = "eta-repo-subpath";     source = [ordered]@{ source = "url"; url = $repoUrl; sha = $PluginRepoSha; path = "nested/eta" } },
            [ordered]@{ name = "theta-repo-unpinned";  source = [ordered]@{ source = "url"; url = $repoUrl } }
        )
    }
    $manifestDir = Join-Path $DestDir ".claude-plugin"
    New-Item -ItemType Directory -Path $manifestDir -Force | Out-Null
    Set-Content -Path (Join-Path $manifestDir "marketplace.json") -Value ($manifest | ConvertTo-Json -Depth 10)

    Push-Location $DestDir
    try {
        & git init -q
        & git config user.email "fixture@agent-extensions.test"
        & git config user.name "agent-extensions fixture"
        & git config uploadpack.allowAnySHA1InWant true
        & git add -A
        & git commit -q -m "Fixture marketplace content"
        return (& git rev-parse HEAD | Out-String).Trim()
    } finally { Pop-Location }
}
