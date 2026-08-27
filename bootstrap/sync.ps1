#!/usr/bin/env pwsh
[CmdletBinding()]
param(
    [string]$RepoRoot = (Split-Path -Parent $PSScriptRoot),
    [string]$CodexSkillsDir = (Join-Path $env:USERPROFILE ".agents\skills"),
    [string]$AntigravityPluginsDir = (Join-Path $env:USERPROFILE ".gemini\config\plugins"),
    [switch]$SkipClaudeCode,
    [switch]$Import  # when set, only defines functions (used by sync.test.ps1)
)

$ErrorActionPreference = "Stop"

function Get-PluginNames {
    param([string]$RepoRoot)
    Get-ChildItem (Join-Path $RepoRoot "plugins") -Directory | Select-Object -ExpandProperty Name
}

function New-OrRepairJunction {
    param([string]$LinkPath, [string]$TargetPath)

    if (Test-Path $LinkPath) {
        $existing = Get-Item $LinkPath
        if (-not $existing.LinkType) {
            throw "Refusing to overwrite '$LinkPath' — it exists and is not a link this script manages."
        }
        $currentTarget = (Get-Item $LinkPath).Target
        if ($currentTarget -eq $TargetPath) {
            return  # already correct, idempotent no-op
        }
        Remove-Item $LinkPath -Force
    }

    try {
        New-Item -ItemType Junction -Path $LinkPath -Target $TargetPath -Force | Out-Null
    } catch {
        throw "Failed to create junction '$LinkPath' -> '$TargetPath': $($_.Exception.Message)"
    }
}

function Get-ExternalMarketplaces {
    param([string]$RepoRoot)
    $path = Join-Path $RepoRoot "bootstrap\external-marketplaces.json"
    if (-not (Test-Path $path)) { return @() }
    $json = Get-Content $path -Raw | ConvertFrom-Json
    if (-not $json.PSObject.Properties['marketplaces']) { return @() }
    return @($json.marketplaces)
}

function Resolve-MarketplaceUrl {
    param([string]$Repo)
    if ($Repo -match '^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$') {
        return "https://github.com/$Repo.git"
    }
    return $Repo
}

function Get-DeclaredPlugins {
    param($Marketplace)
    $out = @()
    foreach ($entry in @($Marketplace.plugins)) {
        if ($entry -is [string]) {
            $out += [PSCustomObject]@{ Name = $entry; ResolvedCommit = "" }
        } else {
            $rc = ""
            if ($entry.PSObject.Properties['resolvedCommit']) { $rc = [string]$entry.resolvedCommit }
            $out += [PSCustomObject]@{ Name = [string]$entry.name; ResolvedCommit = $rc }
        }
    }
    return $out
}

function Get-JsonValue {
    param($Object, [string]$Name)

    if ($null -eq $Object) { return $null }
    if ($Object -is [System.Collections.IDictionary]) {
        if ($Object.Contains($Name)) { return $Object[$Name] }
        return $null
    }
    $prop = $Object.PSObject.Properties[$Name]
    if ($prop) { return $prop.Value }
    return $null
}

function New-PluginSourceResult {
    param([string]$Kind, [string]$Path, [string]$Url, [string]$Sha, [string]$Ref, [string]$SubPath, [string]$ErrorText)
    return [PSCustomObject]@{
        Kind = $Kind; Path = $Path; Url = $Url; Sha = $Sha
        Ref = $Ref; SubPath = $SubPath; Error = $ErrorText
    }
}

function Get-PluginSource {
    param([string]$MarketplaceDir, [string]$PluginName)

    $manifestPath = Join-Path $MarketplaceDir ".claude-plugin\marketplace.json"
    if (-not (Test-Path $manifestPath)) {
        return New-PluginSourceResult -Kind "missing" -Path "" -Url "" -Sha "" -Ref "" -SubPath "" `
            -ErrorText "no .claude-plugin/marketplace.json found at '$MarketplaceDir'"
    }

    try {
        $manifest = Get-Content $manifestPath -Raw | ConvertFrom-Json -AsHashtable
    } catch {
        return New-PluginSourceResult -Kind "missing" -Path "" -Url "" -Sha "" -Ref "" -SubPath "" `
            -ErrorText "could not parse '$manifestPath' — $($_.Exception.Message)"
    }

    $entry = @($manifest['plugins']) | Where-Object { [string](Get-JsonValue $_ 'name') -eq $PluginName } | Select-Object -First 1
    if (-not $entry) {
        return New-PluginSourceResult -Kind "missing" -Path "" -Url "" -Sha "" -Ref "" -SubPath "" `
            -ErrorText "plugin '$PluginName' is not declared in '$manifestPath'"
    }

    $source = Get-JsonValue $entry 'source'
    if ($source -is [string]) {
        return New-PluginSourceResult -Kind "inline" -Path ([string]$source) -Url "" -Sha "" -Ref "" -SubPath "" -ErrorText ""
    }

    $src = $source
    $field = {
        param($Name)
        $value = Get-JsonValue $src $Name
        if ($null -ne $value) { return [string]$value }
        return ""
    }
    $kindField = & $field 'source'
    if ($kindField -ne 'url' -and $kindField -ne 'git-subdir') {
        return New-PluginSourceResult -Kind "missing" -Path "" -Url "" -Sha "" -Ref "" -SubPath "" `
            -ErrorText "plugin '$PluginName' declares unsupported source kind '$kindField'"
    }
    return New-PluginSourceResult -Kind "repo" -Path "" `
        -Url (& $field 'url') -Sha (& $field 'sha') -Ref (& $field 'ref') -SubPath (& $field 'path') -ErrorText ""
}

function Normalize-ExternalMcpServers {
    param($McpServers, [string]$PluginDir)

    $normalized = [ordered]@{}

    foreach ($prop in @($McpServers.PSObject.Properties)) {
        $serverName = $prop.Name
        $server = $prop.Value
        $serverCopy = [ordered]@{}
        $envCopy = [ordered]@{}
        $hasCwd = $false
        $cwd = ""

        foreach ($serverProp in @($server.PSObject.Properties)) {
            switch ($serverProp.Name) {
                'cwd' {
                    $hasCwd = $true
                    $cwd = [string]$serverProp.Value
                }
                'type' { continue }
                '_meta' { continue }
                'env_vars' {
                    foreach ($envVarName in @($serverProp.Value)) {
                        $envVarName = [string]$envVarName
                        if ([string]::IsNullOrWhiteSpace($envVarName)) { continue }
                        $value = [Environment]::GetEnvironmentVariable($envVarName)
                        if (-not [string]::IsNullOrEmpty($value)) {
                            $envCopy[$envVarName] = [string]$value
                        }
                    }
                }
                'env' {
                    foreach ($envProp in @($serverProp.Value.PSObject.Properties)) {
                        $envCopy[$envProp.Name] = [string]$envProp.Value
                    }
                }
                default {
                    $serverCopy[$serverProp.Name] = $serverProp.Value
                }
            }
        }

        if ($hasCwd -and $serverCopy.Contains('args')) {
            $args = @($serverCopy['args'])
            if ($args.Count -gt 0) {
                $firstArg = [string]$args[0]
                if (-not [System.IO.Path]::IsPathRooted($firstArg)) {
                    $baseDir = if ([string]::IsNullOrWhiteSpace($cwd)) { $PluginDir } else { Join-Path $PluginDir $cwd }
                    $args[0] = [System.IO.Path]::GetFullPath((Join-Path $baseDir $firstArg))
                    $serverCopy['args'] = $args
                }
            }
            if (-not $envCopy.Contains('CLAUDE_PLUGIN_ROOT')) {
                $envCopy['CLAUDE_PLUGIN_ROOT'] = $PluginDir
            }
        }

        if ($envCopy.Count -gt 0) {
            $serverCopy['env'] = [PSCustomObject]$envCopy
        }

        $normalized[$serverName] = [PSCustomObject]$serverCopy
    }

    return [PSCustomObject]$normalized
}

function Get-McpServers {
    param($McpJson)

    $servers = Get-JsonValue $McpJson 'mcpServers'
    if ($null -eq $servers) {
        return [PSCustomObject]@{}
    }
    return $servers
}

function Sync-VendorCache {
    param([string]$RepoRoot, [string]$VendorCacheDir)

    $marketplaces = Get-ExternalMarketplaces -RepoRoot $RepoRoot
    $failures = @()

    foreach ($mp in $marketplaces) {
        $dest = Join-Path $VendorCacheDir $mp.name
        $currentSha = $null
        if (Test-Path (Join-Path $dest ".git")) {
            Push-Location $dest
            try { $currentSha = (& git rev-parse HEAD 2>$null | Out-String).Trim() } finally { Pop-Location }
        }
        if ($currentSha -eq $mp.pinnedCommit) { continue }

        if (Test-Path $dest) {
            try {
                Remove-Item $dest -Recurse -Force
            } catch {
                $failures += "Marketplace '$($mp.name)': failed to clear existing vendor-cache directory '$dest' — $($_.Exception.Message)"
                continue
            }
        }
        try {
            New-Item -ItemType Directory -Path $dest -Force | Out-Null
        } catch {
            $failures += "Marketplace '$($mp.name)': failed to create vendor-cache directory '$dest' — $($_.Exception.Message)"
            continue
        }

        $url = Resolve-MarketplaceUrl -Repo $mp.repo
        Push-Location $dest
        try {
            & git init -q 2>&1 | Out-Null
            & git remote add origin $url 2>&1 | Out-Null
            & git fetch --depth 1 origin $mp.pinnedCommit 2>&1 | Out-Null
            if ($LASTEXITCODE -ne 0) {
                $failures += "Marketplace '$($mp.name)': failed to fetch commit '$($mp.pinnedCommit)' from '$url' — it may no longer exist upstream."
                continue
            }
            & git checkout -q FETCH_HEAD 2>&1 | Out-Null
            if ($LASTEXITCODE -ne 0) {
                $failures += "Marketplace '$($mp.name)': failed to check out pinned commit '$($mp.pinnedCommit)'."
            }
        } finally {
            Pop-Location
        }
    }

    return $failures
}

function Sync-PluginRepo {
    param(
        [string]$PluginReposDir,
        [string]$MarketplaceName,
        [string]$PluginName,
        $Source,
        [string]$PinnedCommit
    )

    $fail = {
        param([string]$Message)
        [PSCustomObject]@{ Dir = ""; ResolvedSha = ""; Error = $Message }
    }

    if ($null -eq $Source) {
        return & $fail "plugin '$PluginName' (from '$MarketplaceName'): external source metadata is missing"
    }
    if (-not [string]::IsNullOrWhiteSpace($Source.Error)) {
        return & $fail "plugin '$PluginName' (from '$MarketplaceName'): $($Source.Error)"
    }
    if ($Source.Kind -ne "repo") {
        return & $fail "plugin '$PluginName' (from '$MarketplaceName'): source kind '$($Source.Kind)' is not an external repo"
    }
    if ([string]::IsNullOrWhiteSpace($Source.Url)) {
        return & $fail "plugin '$PluginName' (from '$MarketplaceName'): external source declares no url"
    }

    $wanted = ""
    $wantedCommit = ""
    $wantedRef = ""
    if (-not [string]::IsNullOrWhiteSpace($PinnedCommit)) {
        $wanted = $PinnedCommit
        $wantedCommit = $PinnedCommit
    } elseif (-not [string]::IsNullOrWhiteSpace($Source.Sha)) {
        $wanted = [string]$Source.Sha
        $wantedCommit = [string]$Source.Sha
    } elseif (-not [string]::IsNullOrWhiteSpace($Source.Ref)) {
        $wanted = [string]$Source.Ref
        $wantedRef = [string]$Source.Ref
    }

    $clone = Join-Path $PluginReposDir "$MarketplaceName\$PluginName"
    $current = ""
    if (Test-Path (Join-Path $clone ".git")) {
        Push-Location $clone
        try {
            $current = (& git rev-parse HEAD 2>$null | Out-String).Trim()
            if ($wantedRef -ne "") {
                $wantedCommit = (& git ls-remote --refs --tags --heads origin $wantedRef 2>$null | ForEach-Object {
                    if ($_ -match '^([0-9a-fA-F]{40})\s') { $matches[1] }
                } | Select-Object -First 1 | Out-String).Trim()
            }
        } finally {
            Pop-Location
        }
    }

    $needsFetch = $true
    if ($current -ne "" -and $wantedCommit -ne "" -and $current -eq $wantedCommit) {
        $needsFetch = $false
    }

    if ($needsFetch) {
        if (Test-Path $clone) {
            try {
                Remove-Item $clone -Recurse -Force
            } catch {
                return & $fail "plugin '$PluginName' (from '$MarketplaceName'): could not clear '$clone' — $($_.Exception.Message)"
            }
        }
        try {
            New-Item -ItemType Directory -Path $clone -Force | Out-Null
        } catch {
            return & $fail "plugin '$PluginName' (from '$MarketplaceName'): could not create '$clone' — $($_.Exception.Message)"
        }

        $fetchTarget = if ($wanted -ne "") { $wanted } else { "HEAD" }
        Push-Location $clone
        try {
            & git init -q 2>&1 | Out-Null
            & git remote add origin $Source.Url 2>&1 | Out-Null
            & git fetch --depth 1 origin $fetchTarget 2>&1 | Out-Null
            if ($LASTEXITCODE -ne 0) {
                return & $fail "plugin '$PluginName' (from '$MarketplaceName'): could not fetch '$fetchTarget' from '$($Source.Url)'"
            }
            & git checkout -q FETCH_HEAD 2>&1 | Out-Null
            if ($LASTEXITCODE -ne 0) {
                return & $fail "plugin '$PluginName' (from '$MarketplaceName'): could not check out '$fetchTarget'"
            }
        } finally {
            Pop-Location
        }
    }

    Push-Location $clone
    try {
        $resolved = (& git rev-parse HEAD 2>$null | Out-String).Trim()
    } finally {
        Pop-Location
    }
    if ([string]::IsNullOrWhiteSpace($resolved)) {
        return & $fail "plugin '$PluginName' (from '$MarketplaceName'): clone at '$clone' has no resolvable HEAD"
    }

    $dir = $clone
    if (-not [string]::IsNullOrWhiteSpace($Source.SubPath)) {
        $dir = Join-Path $clone ($Source.SubPath -replace '/', '\')
        if (-not (Test-Path $dir)) {
            return & $fail "plugin '$PluginName' (from '$MarketplaceName'): declared subdirectory '$($Source.SubPath)' does not exist in the clone"
        }
    }

    return [PSCustomObject]@{ Dir = $dir; ResolvedSha = $resolved; Error = "" }
}

function Save-ResolvedCommit {
    param([string]$RepoRoot, [string]$MarketplaceName, [string]$PluginName, [string]$Sha)

    $path = Join-Path $RepoRoot "bootstrap\external-marketplaces.json"
    if (-not (Test-Path $path)) { return "cannot record resolved commit: '$path' does not exist" }

    try {
        $doc = Get-Content $path -Raw | ConvertFrom-Json
    } catch {
        return "cannot record resolved commit: could not parse '$path' — $($_.Exception.Message)"
    }

    $marketplace = @($doc.marketplaces) | Where-Object { $_.name -eq $MarketplaceName } | Select-Object -First 1
    if (-not $marketplace) {
        return "cannot record resolved commit: marketplace '$MarketplaceName' is not declared in '$path'"
    }

    $found = $false
    $newPlugins = @()
    foreach ($entry in @($marketplace.plugins)) {
        $name = if ($entry -is [string]) { $entry } else { [string]$entry.name }
        if ($name -eq $PluginName) {
            $found = $true
            $newPlugins += [PSCustomObject]@{
                name = $name
                resolvedCommit = $Sha
            }
        } else {
            $newPlugins += $entry
        }
    }

    if (-not $found) {
        return "cannot record resolved commit: plugin '$PluginName' is not declared under marketplace '$MarketplaceName'"
    }

    $marketplace.plugins = $newPlugins
    try {
        Set-Content -Path $path -Value ($doc | ConvertTo-Json -Depth 12)
    } catch {
        return "cannot record resolved commit: failed to write '$path' — $($_.Exception.Message)"
    }

    return ""
}

function Resolve-PluginDir {
    param([string]$RepoRoot, [string]$VendorCacheDir, $Marketplace, $DeclaredPlugin)

    $name = $DeclaredPlugin.Name
    $marketplaceDir = Join-Path $VendorCacheDir $Marketplace.name
    $src = Get-PluginSource -MarketplaceDir $marketplaceDir -PluginName $name

    if ($src.Kind -eq "missing") {
        return [PSCustomObject]@{ Dir = ""; Failure = "Plugin '$name' (from '$($Marketplace.name)'): $($src.Error)" }
    }

    if ($src.Kind -eq "inline") {
        $rel = $src.Path -replace '^\./','' -replace '/','\'
        $dir = Join-Path $marketplaceDir $rel
        if (-not (Test-Path $dir)) {
            return [PSCustomObject]@{ Dir = ""; Failure = "Plugin '$name' (from '$($Marketplace.name)'): declared inline path '$($src.Path)' does not exist in the marketplace clone" }
        }
        return [PSCustomObject]@{ Dir = $dir; Failure = "" }
    }

    $pluginReposDir = Join-Path $VendorCacheDir "_plugins"
    $r = Sync-PluginRepo -PluginReposDir $pluginReposDir -MarketplaceName $Marketplace.name `
        -PluginName $name -Source $src -PinnedCommit $DeclaredPlugin.ResolvedCommit
    if ($r.Error -ne "") {
        return [PSCustomObject]@{ Dir = ""; Failure = $r.Error }
    }

    if ([string]::IsNullOrWhiteSpace($src.Sha) -and [string]::IsNullOrWhiteSpace($src.Ref) `
        -and [string]::IsNullOrWhiteSpace($DeclaredPlugin.ResolvedCommit)) {
        $saveErr = Save-ResolvedCommit -RepoRoot $RepoRoot -MarketplaceName $Marketplace.name -PluginName $name -Sha $r.ResolvedSha
        if ($saveErr -ne "") {
            return [PSCustomObject]@{ Dir = ""; Failure = $saveErr }
        }
        Write-Host "Pinned '$name' (from '$($Marketplace.name)') to $($r.ResolvedSha)"
    }

    return [PSCustomObject]@{ Dir = $r.Dir; Failure = "" }
}

function ConvertTo-TomlString {
    param([string]$Value)
    $escaped = $Value -replace '\\', '\\' -replace '"', '\"'
    return '"' + $escaped + '"'
}

function ConvertTo-CodexMcpToml {
    param($McpServers)

    $stdioAllowed = @('command', 'args', 'env')
    $httpAllowed = @('url', 'headers')
    $lines = @()

    foreach ($prop in $McpServers.PSObject.Properties) {
        $serverName = $prop.Name
        $server = $prop.Value
        $propNames = @($server.PSObject.Properties.Name)
        $hasCommand = $propNames -contains 'command'
        $hasUrl = $propNames -contains 'url'

        if (-not $hasCommand -and -not $hasUrl) {
            throw "MCP server '$serverName' has neither 'command' (stdio) nor 'url' (http) — unrecognized server shape."
        }
        if ($hasCommand -and $hasUrl) {
            throw "MCP server '$serverName' has both 'command' and 'url' — ambiguous transport, cannot translate."
        }

        $allowed = if ($hasCommand) { $stdioAllowed } else { $httpAllowed }
        $unknown = @($propNames | Where-Object { $allowed -notcontains $_ })
        if ($unknown.Count -gt 0) {
            throw "MCP server '$serverName' has unrecognized field(s): $($unknown -join ', ')."
        }

        $lines += "[mcp_servers.$serverName]"
        if ($hasCommand) {
            $lines += "command = $(ConvertTo-TomlString $server.command)"
            if ($propNames -contains 'args') {
                $argStrings = @($server.args | ForEach-Object { ConvertTo-TomlString $_ })
                $lines += "args = [" + ($argStrings -join ", ") + "]"
            }
            if ($propNames -contains 'env') {
                $envPairs = @($server.env.PSObject.Properties | ForEach-Object {
                    "$($_.Name) = $(ConvertTo-TomlString $_.Value)"
                })
                $lines += "env = { " + ($envPairs -join ", ") + " }"
            }
        } else {
            $lines += "url = $(ConvertTo-TomlString $server.url)"
            if ($propNames -contains 'headers') {
                $headerPairs = @($server.headers.PSObject.Properties | ForEach-Object {
                    "$($_.Name) = $(ConvertTo-TomlString $_.Value)"
                })
                $lines += "http_headers = { " + ($headerPairs -join ", ") + " }"
            }
        }
        $lines += ""
    }
    return ($lines -join "`n").Trim()
}

function ConvertTo-AntigravityMcpConfig {
    param($McpServers)

    $stdioAllowed = @('command', 'args', 'env')
    $httpAllowed = @('url', 'headers')

    foreach ($prop in $McpServers.PSObject.Properties) {
        $serverName = $prop.Name
        $server = $prop.Value
        $propNames = @($server.PSObject.Properties.Name)
        $hasCommand = $propNames -contains 'command'
        $hasUrl = $propNames -contains 'url'

        if (-not $hasCommand -and -not $hasUrl) {
            throw "MCP server '$serverName' has neither 'command' (stdio) nor 'url' (http) — unrecognized server shape."
        }
        if ($hasCommand -and $hasUrl) {
            throw "MCP server '$serverName' has both 'command' and 'url' — ambiguous transport, cannot translate."
        }
        $allowed = if ($hasCommand) { $stdioAllowed } else { $httpAllowed }
        $unknown = @($propNames | Where-Object { $allowed -notcontains $_ })
        if ($unknown.Count -gt 0) {
            throw "MCP server '$serverName' has unrecognized field(s): $($unknown -join ', ')."
        }
    }

    $wrapped = [PSCustomObject]@{ mcpServers = $McpServers }
    return ($wrapped | ConvertTo-Json -Depth 10)
}

function Merge-CodexMcpConfig {
    param([string]$ConfigPath, [hashtable]$TomlByPlugin)

    $beginMarker = "# >>> agent-extensions managed mcp_servers (do not edit within this block) >>>"
    $endMarker = "# <<< agent-extensions managed mcp_servers <<<"

    $existingLines = @()
    if (Test-Path $ConfigPath) {
        $existingLines = @(Get-Content $ConfigPath)
    }

    $beginIdx = -1
    $endIdx = -1
    for ($i = 0; $i -lt $existingLines.Count; $i++) {
        if ($existingLines[$i] -eq $beginMarker) { $beginIdx = $i; break }
    }
    if ($beginIdx -ge 0) {
        for ($i = $beginIdx + 1; $i -lt $existingLines.Count; $i++) {
            if ($existingLines[$i] -eq $endMarker) { $endIdx = $i; break }
        }
    }

    if ($beginIdx -ge 0 -and $endIdx -ge 0) {
        $beforeLines = if ($beginIdx -gt 0) { @($existingLines[0..($beginIdx - 1)]) } else { @() }
        $afterLines = if ($endIdx + 1 -lt $existingLines.Count) { @($existingLines[($endIdx + 1)..($existingLines.Count - 1)]) } else { @() }
        $before = ($beforeLines -join "`n").TrimEnd()
        $after = ($afterLines -join "`n").TrimStart()
        $parts = @($before, $after) | Where-Object { $_ -ne "" }
        $existing = $parts -join "`n`n"
    } else {
        $existing = ($existingLines -join "`n").TrimEnd()
    }

    $blockLines = @($beginMarker)
    foreach ($plugin in ($TomlByPlugin.Keys | Sort-Object)) {
        $toml = $TomlByPlugin[$plugin]
        if ([string]::IsNullOrWhiteSpace($toml)) { continue }
        $blockLines += "# plugin: $plugin"
        $blockLines += $toml.TrimEnd()
        $blockLines += ""
    }
    $blockLines += $endMarker
    $block = ($blockLines -join "`n")

    $parentDir = Split-Path -Parent $ConfigPath
    if (-not (Test-Path $parentDir)) {
        New-Item -ItemType Directory -Path $parentDir -Force | Out-Null
    }

    $final = if ([string]::IsNullOrWhiteSpace($existing)) { "$block`n" } else { "$existing`n`n$block`n" }
    Set-Content -Path $ConfigPath -Value $final -NoNewline
}

function Write-AntigravityMcpConfig {
    param([string]$PluginStagedDir, [string]$JsonContent)
    if ([string]::IsNullOrWhiteSpace($JsonContent)) { return }
    if (-not (Test-Path $PluginStagedDir)) {
        New-Item -ItemType Directory -Path $PluginStagedDir -Force | Out-Null
    }
    $target = Join-Path $PluginStagedDir "mcp_config.json"
    Set-Content -Path $target -Value $JsonContent -NoNewline
}

function Sync-CodexSkills {
    param([string]$RepoRoot, [string]$CodexSkillsDir)

    if (-not (Test-Path $CodexSkillsDir)) {
        New-Item -ItemType Directory -Path $CodexSkillsDir -Force | Out-Null
    }

    foreach ($plugin in (Get-PluginNames -RepoRoot $RepoRoot)) {
        $skillsRoot = Join-Path $RepoRoot "plugins\$plugin\skills"
        if (-not (Test-Path $skillsRoot)) { continue }
        Get-ChildItem $skillsRoot -Directory | ForEach-Object {
            $target = $_.FullName
            $link = Join-Path $CodexSkillsDir $_.Name
            New-OrRepairJunction -LinkPath $link -TargetPath $target
        }
    }

    return $true
}

function Sync-ExternalCodexContent {
    param([string]$RepoRoot, [string]$VendorCacheDir, [string]$CodexSkillsDir, [string]$CodexConfigPath)

    $marketplaces = Get-ExternalMarketplaces -RepoRoot $RepoRoot
    $failures = @()
    $tomlByPlugin = @{}

    foreach ($mp in $marketplaces) {
        foreach ($declared in (Get-DeclaredPlugins -Marketplace $mp)) {
            $pluginName = $declared.Name
            $resolved = Resolve-PluginDir -RepoRoot $RepoRoot -VendorCacheDir $VendorCacheDir -Marketplace $mp -DeclaredPlugin $declared
            if ($resolved.Failure -ne "") { $failures += $resolved.Failure; continue }
            $pluginDir = $resolved.Dir

            $skillsRoot = Join-Path $pluginDir "skills"
            if (Test-Path $skillsRoot) {
                Get-ChildItem $skillsRoot -Directory | ForEach-Object {
                    try {
                        $link = Join-Path $CodexSkillsDir $_.Name
                        New-OrRepairJunction -LinkPath $link -TargetPath $_.FullName
                    } catch {
                        $failures += "Plugin '$pluginName' (from '$($mp.name)'): failed to link skill '$($_.Name)' — $($_.Exception.Message)"
                    }
                }
            }

            $mcpPath = Join-Path $pluginDir ".mcp.json"
            if (Test-Path $mcpPath) {
                try {
                    $mcpJson = Get-Content $mcpPath -Raw | ConvertFrom-Json
                    $servers = if ($mcpJson.PSObject.Properties['mcpServers']) { Normalize-ExternalMcpServers -McpServers $mcpJson.mcpServers -PluginDir $pluginDir } else { [PSCustomObject]@{} }
                    $toml = ConvertTo-CodexMcpToml -McpServers $servers
                    if (-not [string]::IsNullOrWhiteSpace($toml)) {
                        $tomlByPlugin[$pluginName] = $toml
                    }
                } catch {
                    $failures += "Plugin '$pluginName' (from '$($mp.name)'): MCP translation to Codex TOML failed — $($_.Exception.Message)"
                }
            }
        }
    }

    if ($tomlByPlugin.Count -gt 0) {
        try {
            Merge-CodexMcpConfig -ConfigPath $CodexConfigPath -TomlByPlugin $tomlByPlugin
        } catch {
            $failures += "Failed to merge translated MCP servers into '$CodexConfigPath' — $($_.Exception.Message)"
        }
    }

    return $failures
}

function Sync-AntigravityPlugins {
    param([string]$RepoRoot, [string]$AntigravityPluginsDir)

    if (-not (Test-Path $AntigravityPluginsDir)) {
        New-Item -ItemType Directory -Path $AntigravityPluginsDir -Force | Out-Null
    }

    foreach ($plugin in (Get-PluginNames -RepoRoot $RepoRoot)) {
        $target = Join-Path $RepoRoot "plugins\$plugin"
        $link = Join-Path $AntigravityPluginsDir $plugin
        New-OrRepairJunction -LinkPath $link -TargetPath $target
    }

    return $true
}

function Sync-ExternalAntigravityContent {
    param([string]$RepoRoot, [string]$VendorCacheDir, [string]$StagedDir, [string]$AntigravityPluginsDir)

    $marketplaces = Get-ExternalMarketplaces -RepoRoot $RepoRoot
    $failures = @()

    foreach ($mp in $marketplaces) {
        foreach ($declared in (Get-DeclaredPlugins -Marketplace $mp)) {
            $pluginName = $declared.Name
            $resolved = Resolve-PluginDir -RepoRoot $RepoRoot -VendorCacheDir $VendorCacheDir -Marketplace $mp -DeclaredPlugin $declared
            if ($resolved.Failure -ne "") { $failures += $resolved.Failure; continue }
            $pluginDir = $resolved.Dir
            $stagedPluginDir = Join-Path $StagedDir "antigravity\$pluginName"

            try {
                if (-not (Test-Path $stagedPluginDir)) {
                    New-Item -ItemType Directory -Path $stagedPluginDir -Force | Out-Null
                }

                $skillsSource = Join-Path $pluginDir "skills"
                if (Test-Path $skillsSource) {
                    $skillsStagedLink = Join-Path $stagedPluginDir "skills"
                    New-OrRepairJunction -LinkPath $skillsStagedLink -TargetPath $skillsSource
                }

                $mcpPath = Join-Path $pluginDir ".mcp.json"
                if (Test-Path $mcpPath) {
                    $mcpJson = Get-Content $mcpPath -Raw | ConvertFrom-Json
                    $servers = if ($mcpJson.PSObject.Properties['mcpServers']) { Normalize-ExternalMcpServers -McpServers $mcpJson.mcpServers -PluginDir $pluginDir } else { [PSCustomObject]@{} }
                    $config = ConvertTo-AntigravityMcpConfig -McpServers $servers
                    if (-not [string]::IsNullOrWhiteSpace($config)) {
                        Write-AntigravityMcpConfig -PluginStagedDir $stagedPluginDir -JsonContent $config
                    }
                }

                $finalLink = Join-Path $AntigravityPluginsDir $pluginName
                New-OrRepairJunction -LinkPath $finalLink -TargetPath $stagedPluginDir
            } catch {
                $failures += "Plugin '$pluginName' (from '$($mp.name)'): $($_.Exception.Message)"
            }
        }
    }

    return $failures
}

function Sync-ClaudeCodeMarketplace {
    param([string]$RepoRoot)

    # Every native `& claude ...` call below is piped to Out-Null. In
    # PowerShell, any uncaptured output from a statement inside a function
    # becomes part of that function's return value once the function is
    # called via assignment (`$x = Sync-ClaudeCodeMarketplace ...`) — not
    # just what's passed to `return`. Without suppression, the CLI's own
    # status lines (printed on every call, including successful no-ops)
    # would flood $failures and make it permanently non-empty.
    & claude plugin marketplace add $RepoRoot | Out-Null
    if ($LASTEXITCODE -ne 0) {
        throw "claude plugin marketplace add '$RepoRoot' failed with exit code $LASTEXITCODE"
    }

    foreach ($plugin in (Get-PluginNames -RepoRoot $RepoRoot)) {
        & claude plugin install "$plugin@agent-extensions" | Out-Null
        if ($LASTEXITCODE -ne 0) {
            throw "claude plugin install '$plugin@agent-extensions' failed with exit code $LASTEXITCODE"
        }
    }

    $failures = @()
    foreach ($mp in (Get-ExternalMarketplaces -RepoRoot $RepoRoot)) {
        & claude plugin marketplace add $mp.repo | Out-Null
        if ($LASTEXITCODE -ne 0) {
            $failures += "claude plugin marketplace add '$($mp.repo)' failed with exit code $LASTEXITCODE"
            continue
        }
        foreach ($declared in (Get-DeclaredPlugins -Marketplace $mp)) {
            $pluginName = $declared.Name
            & claude plugin install "$pluginName@$($mp.name)" | Out-Null
            if ($LASTEXITCODE -ne 0) {
                $failures += "claude plugin install '$pluginName@$($mp.name)' failed with exit code $LASTEXITCODE"
            }
        }
    }
    return $failures
}

if ($Import) { return }

$vendorCacheDir = Join-Path $RepoRoot ".vendor-cache"
$stagedDir = Join-Path $vendorCacheDir "_staged"
$codexConfigPath = Join-Path $env:USERPROFILE ".codex\config.toml"

Sync-CodexSkills -RepoRoot $RepoRoot -CodexSkillsDir $CodexSkillsDir | Out-Null
Sync-AntigravityPlugins -RepoRoot $RepoRoot -AntigravityPluginsDir $AntigravityPluginsDir | Out-Null

$allFailures = @()
$allFailures += (Sync-VendorCache -RepoRoot $RepoRoot -VendorCacheDir $vendorCacheDir)
$allFailures += (Sync-ExternalCodexContent -RepoRoot $RepoRoot -VendorCacheDir $vendorCacheDir -CodexSkillsDir $CodexSkillsDir -CodexConfigPath $codexConfigPath)
$allFailures += (Sync-ExternalAntigravityContent -RepoRoot $RepoRoot -VendorCacheDir $vendorCacheDir -StagedDir $stagedDir -AntigravityPluginsDir $AntigravityPluginsDir)

if (-not $SkipClaudeCode) {
    $allFailures += (Sync-ClaudeCodeMarketplace -RepoRoot $RepoRoot)
}

if ($allFailures.Count -gt 0) {
    Write-Output "Sync completed with failures:"
    $allFailures | ForEach-Object { Write-Output "  - $_" }
    exit 1
}

Write-Output "Sync complete."
