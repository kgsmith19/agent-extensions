function New-FixtureMarketplace {
    param([string]$DestDir)

    if (Test-Path $DestDir) { Remove-Item $DestDir -Recurse -Force }
    New-Item -ItemType Directory -Path $DestDir -Force | Out-Null

    $alphaSkill = Join-Path $DestDir "alpha-skills\skills\greet"
    New-Item -ItemType Directory -Path $alphaSkill -Force | Out-Null
    Set-Content -Path (Join-Path $alphaSkill "SKILL.md") -Value @"
---
name: greet
description: Says hello. Fixture skill for agent-extensions sync tests.
---

Say hello to the user.
"@

    $beta = Join-Path $DestDir "beta-mcp-stdio"
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

    $gamma = Join-Path $DestDir "gamma-mcp-http"
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

    $delta = Join-Path $DestDir "delta-malformed"
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

    $epsilon = Join-Path $DestDir "epsilon-invalid-json"
    New-Item -ItemType Directory -Path $epsilon -Force | Out-Null
    Set-Content -Path (Join-Path $epsilon ".mcp.json") -Value '{ "mcpServers": { "broken": } }'

    Push-Location $DestDir
    try {
        & git init -q
        & git config user.email "fixture@agent-extensions.test"
        & git config user.name "agent-extensions fixture"
        & git add -A
        & git commit -q -m "Fixture marketplace content"
        $sha = (& git rev-parse HEAD).Trim()
    } finally {
        Pop-Location
    }

    return $sha
}
