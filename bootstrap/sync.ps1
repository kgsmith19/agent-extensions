#!/usr/bin/env pwsh
[CmdletBinding()]
param(
    [string]$RepoRoot = (Split-Path -Parent $PSScriptRoot),
    [string]$CodexSkillsDir = (Join-Path $HOME ".agents\skills"),
    [string]$CodexAgentsDir = (Join-Path $HOME ".codex\agents"),
    [string]$AntigravityPluginsDir = (Join-Path $HOME ".gemini\config\plugins"),
    [string]$AntigravityAgentsDir = (Join-Path $HOME ".gemini\config\agents"),
    [switch]$SkipClaudeCode,
    [switch]$Import,  # when set, only defines functions (used by sync.test.ps1)
    [switch]$ConfirmAccountApplied
)

$CodexSkillsDirOverridden = $PSBoundParameters.ContainsKey('CodexSkillsDir')
$AntigravityPluginsDirOverridden = $PSBoundParameters.ContainsKey('AntigravityPluginsDir')

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

    # Junctions are an NTFS-only concept and don't exist on Linux/macOS.
    # Windows uses a junction specifically because it needs neither Admin
    # nor Developer Mode, unlike a Windows symlink. Non-Windows has no such
    # restriction, so a real symlink there is the direct equivalent of what
    # sync.sh already creates with `ln -s`.
    $linkType = if ($IsWindows) { "Junction" } else { "SymbolicLink" }
    try {
        New-Item -ItemType $linkType -Path $LinkPath -Target $TargetPath -Force | Out-Null
    } catch {
        throw "Failed to create $linkType '$LinkPath' -> '$TargetPath': $($_.Exception.Message)"
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

# --- Agent translation (Claude markdown -> Codex TOML / Antigravity MD) ---
#
# Real plugin data includes YAML frontmatter this codebase has no library
# to parse safely: multi-line block scalars (`description: |`), quoted
# values with embedded punctuation, files with no `name:` at all. Rather
# than hand-roll a YAML parser and risk silently mangling those (the exact
# failure mode Spec 1's amendment had to fix elsewhere), this only
# translates single-line scalar values it can extract with confidence and
# reports anything else as a declared per-agent failure — never a guess.
#
# Deliberately NOT translated: `tools` and `model`. Claude's tool names
# (Read, Grep, Bash, ...) and model aliases (sonnet, opus) have no
# principled mapping to Codex's or Antigravity's own tool/model
# vocabularies — carrying them over as literal strings would silently
# produce wrong (not just incomplete) configuration. Translated agents
# get the target provider's default tools and default model instead.

function Get-YamlScalar {
    param([string]$Raw)
    $Raw = $Raw.Trim()
    if ($Raw -eq "" -or $Raw -eq "|" -or $Raw -eq "|-" -or $Raw -eq "|+" -or $Raw -eq ">" -or $Raw -eq ">-" -or $Raw -eq ">+") {
        return "__COMPLEX__"
    }
    if ($Raw.Length -ge 2 -and $Raw.StartsWith("'") -and $Raw.EndsWith("'")) {
        return $Raw.Substring(1, $Raw.Length - 2) -replace "''", "'"
    }
    if ($Raw.Length -ge 2 -and $Raw.StartsWith('"') -and $Raw.EndsWith('"')) {
        return $Raw.Substring(1, $Raw.Length - 2)
    }
    return $Raw
}

function Get-AgentFrontmatter {
    param([string]$AgentMdPath)
    $lines = Get-Content $AgentMdPath
    if ($lines.Count -eq 0 -or $lines[0].Trim() -ne "---") {
        return [PSCustomObject]@{ Name = ""; Description = ""; ErrorText = "no YAML frontmatter (file does not start with '---')" }
    }
    $endIdx = -1
    for ($i = 1; $i -lt $lines.Count; $i++) {
        if ($lines[$i].Trim() -eq "---") { $endIdx = $i; break }
    }
    $frontmatter = if ($endIdx -gt 1) { $lines[1..($endIdx - 1)] } else { @() }

    $nameLine = $frontmatter | Where-Object { $_ -match '^name:' } | Select-Object -First 1
    $descLine = $frontmatter | Where-Object { $_ -match '^description:' } | Select-Object -First 1

    if (-not $nameLine) {
        $name = [System.IO.Path]::GetFileNameWithoutExtension($AgentMdPath)
    } else {
        $name = Get-YamlScalar ($nameLine -replace '^name:', '')
        if ($name -eq "__COMPLEX__") {
            return [PSCustomObject]@{ Name = ""; Description = ""; ErrorText = "'name' is not a simple single-line value" }
        }
    }

    if (-not $descLine) {
        $description = ""
    } else {
        $description = Get-YamlScalar ($descLine -replace '^description:', '')
        if ($description -eq "__COMPLEX__") {
            return [PSCustomObject]@{ Name = ""; Description = ""; ErrorText = "'description' is a multi-line/block YAML value — not mechanically translatable" }
        }
    }

    return [PSCustomObject]@{ Name = $name; Description = $description; ErrorText = "" }
}

function Get-AgentBody {
    param([string]$AgentMdPath)
    $lines = Get-Content $AgentMdPath
    $seen = 0
    $body = @()
    foreach ($line in $lines) {
        if ($line.Trim() -eq "---") { $seen++; continue }
        if ($seen -ge 2) { $body += $line }
    }
    return ($body -join "`n")
}

function ConvertTo-CodexAgentToml {
    param([string]$AgentMdPath, [string]$PluginName)
    $parsed = Get-AgentFrontmatter -AgentMdPath $AgentMdPath
    if ($parsed.ErrorText -ne "") {
        Write-Host "Agent '$(Split-Path -Leaf $AgentMdPath)' (from '$PluginName'): $($parsed.ErrorText)"
        return $null
    }
    $qualifiedName = "$PluginName-$($parsed.Name)"
    $body = Get-AgentBody -AgentMdPath $AgentMdPath

    if ($body.Contains("'''")) {
        Write-Host "Agent '$qualifiedName': system prompt contains a literal ''' sequence, which TOML literal strings cannot safely embed"
        return $null
    }

    $nameToml = $qualifiedName | ConvertTo-Json -Compress
    $descToml = $parsed.Description | ConvertTo-Json -Compress
    return "name = $nameToml`ndescription = $descToml`ndeveloper_instructions = '''`n$body`n'''`n"
}

function ConvertTo-AntigravityAgentMd {
    param([string]$AgentMdPath, [string]$PluginName)
    $parsed = Get-AgentFrontmatter -AgentMdPath $AgentMdPath
    if ($parsed.ErrorText -ne "") {
        Write-Host "Agent '$(Split-Path -Leaf $AgentMdPath)' (from '$PluginName'): $($parsed.ErrorText)"
        return $null
    }
    $qualifiedName = "$PluginName-$($parsed.Name)"
    $body = Get-AgentBody -AgentMdPath $AgentMdPath

    $nameYaml = $qualifiedName | ConvertTo-Json -Compress
    $descYaml = $parsed.Description | ConvertTo-Json -Compress
    return "---`nname: $nameYaml`ndescription: $descYaml`n---`n`n$body`n"
}

function Sync-PluginAgentsCodex {
    param([string]$PluginDir, [string]$PluginName, [string]$CodexAgentsDir)
    $agentsRoot = Join-Path $PluginDir "agents"
    if (-not (Test-Path $agentsRoot)) { return $true }

    $ok = $true
    foreach ($agentFile in (Get-ChildItem $agentsRoot -Filter "*.md" -File -ErrorAction SilentlyContinue)) {
        $toml = ConvertTo-CodexAgentToml -AgentMdPath $agentFile.FullName -PluginName $PluginName
        if ($null -eq $toml) { $ok = $false; continue }
        if (-not (Test-Path $CodexAgentsDir)) { New-Item -ItemType Directory -Path $CodexAgentsDir -Force | Out-Null }
        $base = [System.IO.Path]::GetFileNameWithoutExtension($agentFile.Name)
        Set-Content -Path (Join-Path $CodexAgentsDir "$PluginName-$base.toml") -Value $toml -NoNewline
    }
    return $ok
}

function Sync-PluginAgentsAntigravity {
    param([string]$PluginDir, [string]$PluginName, [string]$AntigravityAgentsDir)
    $agentsRoot = Join-Path $PluginDir "agents"
    if (-not (Test-Path $agentsRoot)) { return $true }

    $ok = $true
    foreach ($agentFile in (Get-ChildItem $agentsRoot -Filter "*.md" -File -ErrorAction SilentlyContinue)) {
        $md = ConvertTo-AntigravityAgentMd -AgentMdPath $agentFile.FullName -PluginName $PluginName
        if ($null -eq $md) { $ok = $false; continue }
        if (-not (Test-Path $AntigravityAgentsDir)) { New-Item -ItemType Directory -Path $AntigravityAgentsDir -Force | Out-Null }
        $base = [System.IO.Path]::GetFileNameWithoutExtension($agentFile.Name)
        Set-Content -Path (Join-Path $AntigravityAgentsDir "$PluginName-$base.md") -Value $md -NoNewline
    }
    return $ok
}

# --- Hook translation (Claude hooks.json -> Codex / Antigravity) ---
#
# Ported only for events confirmed to exist in the target's own vocabulary
# (developers.openai.com/codex/hooks; antigravity.google/docs/hooks/) —
# every other declared event is left alone, not guessed at. Both targets'
# hooks.json shapes were confirmed against official docs before writing
# any of this, following the same discipline as the agent translators.
#
# ~/.codex/hooks.json is one file shared by every plugin, and JSON has no
# comment syntax to mark a managed region the way config.toml's TOML
# merge does — so this tool treats that whole file as fully managed and
# regenerates it from the current roster every sync, same as it already
# treats the Antigravity plugins folder. A hand-written Codex hooks.json
# belongs in a different config layer (e.g. a project's own .codex/), not
# merged into this one.

$script:CodexHookEvents = @("PreToolUse", "PostToolUse", "Stop", "UserPromptSubmit", "SessionStart")
$script:AntigravityHookEvents = @("PreToolUse", "PostToolUse", "Stop")
$script:HookEnvWrapper = Join-Path $PSScriptRoot "hook_env_wrapper.py"

# Neither Codex's nor Antigravity's hooks.json schema has an env field
# (confirmed against their published docs) — only Claude Code sets
# CLAUDE_PLUGIN_ROOT as an actual environment variable for the spawned hook
# process, in addition to substituting it into the command string. A hook
# script that reads $env:CLAUDE_PLUGIN_ROOT / os.environ['CLAUDE_PLUGIN_ROOT']
# itself (e.g. to import sibling modules, as hookify's hooks/*.py do for
# their core/ package) silently no-ops on both targets without this. Only
# rewrites the one confirmed-real shape (`python3 "<script>"`, nothing else
# appended) produced by substituting ${CLAUDE_PLUGIN_ROOT} into a plugin's
# own hooks.json — anything else is left untouched rather than guessed at.
function ConvertTo-WrappedHookCommand {
    param([string]$Command, [string]$PluginDir)
    if ($Command -match '^python3?\s+"([^"]+)"$') {
        $scriptPath = $Matches[1]
        return "python3 `"$script:HookEnvWrapper`" `"$PluginDir`" `"$scriptPath`""
    }
    return $Command
}

function Convert-HookEntriesForTarget {
    param($HooksObj, [string[]]$Events, [string]$PluginDir)
    $result = [ordered]@{}
    if ($null -eq $HooksObj) { return $result }
    foreach ($prop in $HooksObj.PSObject.Properties) {
        if ($prop.Name -notin $Events) { continue }
        $entries = @()
        foreach ($entry in @($prop.Value)) {
            $newEntry = [ordered]@{}
            if ($entry.PSObject.Properties['matcher']) { $newEntry['matcher'] = $entry.matcher }
            $newHooks = @()
            foreach ($h in @($entry.hooks)) {
                $nh = [ordered]@{}
                if ($h.PSObject.Properties['timeout']) { $nh['timeout'] = $h.timeout }
                $nh['type'] = if ($h.PSObject.Properties['type']) { $h.type } else { "command" }
                $cmd = if ($h.PSObject.Properties['command']) { $h.command } else { "" }
                $cmd = $cmd.Replace('${CLAUDE_PLUGIN_ROOT}', $PluginDir)
                $nh['command'] = ConvertTo-WrappedHookCommand -Command $cmd -PluginDir $PluginDir
                $newHooks += [PSCustomObject]$nh
            }
            $newEntry['hooks'] = $newHooks
            $entries += [PSCustomObject]$newEntry
        }
        $result[$prop.Name] = $entries
    }
    return $result
}

function ConvertTo-CodexHooksJson {
    param([string]$HooksJsonPath, [string]$PluginDir)
    try {
        $raw = Get-Content $HooksJsonPath -Raw | ConvertFrom-Json
    } catch {
        Write-Host "Failed to parse '$HooksJsonPath' as JSON"
        return $null
    }
    $hooksObj = if ($raw.PSObject.Properties['hooks']) { $raw.hooks } else { $null }
    return Convert-HookEntriesForTarget -HooksObj $hooksObj -Events $script:CodexHookEvents -PluginDir $PluginDir
}

function Merge-CodexHooksJson {
    param([string]$HooksConfigPath, [hashtable]$HooksByPlugin)

    $combined = [ordered]@{}
    foreach ($plugin in $HooksByPlugin.Keys) {
        $pluginHooks = $HooksByPlugin[$plugin]
        foreach ($key in $pluginHooks.Keys) {
            if (-not $combined.Contains($key)) { $combined[$key] = @() }
            $combined[$key] = @($combined[$key]) + @($pluginHooks[$key])
        }
    }

    $dir = Split-Path -Parent $HooksConfigPath
    if ($dir -and -not (Test-Path $dir)) { New-Item -ItemType Directory -Path $dir -Force | Out-Null }
    (@{ hooks = $combined } | ConvertTo-Json -Depth 12) | Set-Content -Path $HooksConfigPath
}

function ConvertTo-AntigravityHooksJson {
    param([string]$HooksJsonPath, [string]$PluginDir, [string]$PluginName)
    try {
        $raw = Get-Content $HooksJsonPath -Raw | ConvertFrom-Json
    } catch {
        Write-Host "Failed to parse '$HooksJsonPath' as JSON"
        return $null
    }
    $hooksObj = if ($raw.PSObject.Properties['hooks']) { $raw.hooks } else { $null }
    $events = Convert-HookEntriesForTarget -HooksObj $hooksObj -Events $script:AntigravityHookEvents -PluginDir $PluginDir
    if ($events.Count -eq 0) { return "" }
    $wrapper = [ordered]@{ $PluginName = $events }
    return ($wrapper | ConvertTo-Json -Depth 12)
}

function Write-AntigravityHooksJson {
    param([string]$PluginStagedDir, [string]$JsonContent)
    if ([string]::IsNullOrWhiteSpace($JsonContent)) { return }
    if (-not (Test-Path $PluginStagedDir)) { New-Item -ItemType Directory -Path $PluginStagedDir -Force | Out-Null }
    Set-Content -Path (Join-Path $PluginStagedDir "hooks.json") -Value $JsonContent -NoNewline
}

# --- Commands gap report ---
#
# Hand re-authoring ~100 commands across 39 plugins as skills is an
# explicit non-goal (re-forking on every upstream update, a content
# project rather than mechanical translation) — this just names what
# exists and isn't ported, per plugin, so the gap is visible rather than
# silently absent.

function New-CommandGapReport {
    param([string]$RepoRoot, [string]$VendorCacheDir)
    $reportPath = Join-Path $RepoRoot "bootstrap\command-gap-report.md"
    $lines = @(
        "# Command gap report", "",
        "Generated by bootstrap/sync from the declared external roster.",
        "Commands are Claude Code slash-command definitions. Hand re-authoring",
        "them as skills for Codex/Antigravity is an explicit non-goal (see",
        "``docs/superpowers/specs/2026-08-27-completion-milestone-design.md``,",
        "Spec 4) — this names what exists and is not ported, per plugin,",
        "rather than leaving the gap silent.", ""
    )
    $total = 0
    $pluginsWithCommands = 0

    foreach ($mp in (Get-ExternalMarketplaces -RepoRoot $RepoRoot)) {
        foreach ($declared in (Get-DeclaredPlugins -Marketplace $mp)) {
            $pluginName = $declared.Name
            $resolved = Resolve-PluginDir -RepoRoot $RepoRoot -VendorCacheDir $VendorCacheDir -Marketplace $mp -DeclaredPlugin $declared
            if ($resolved.Failure -ne "") { continue }
            $commandsRoot = Join-Path $resolved.Dir "commands"
            if (-not (Test-Path $commandsRoot)) { continue }
            $names = @(Get-ChildItem $commandsRoot -Filter "*.md" -File -ErrorAction SilentlyContinue | ForEach-Object { $_.BaseName })
            if ($names.Count -eq 0) { continue }
            $total += $names.Count
            $pluginsWithCommands++
            $lines += "- **$pluginName** (from $($mp.name)): $($names -join ' ')"
        }
    }

    $lines += ""
    $lines += "$pluginsWithCommands plugin(s), $total command(s) total — none ported."
    Set-Content -Path $reportPath -Value $lines
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

        # Antigravity's schema requires "serverUrl" for HTTP servers — "url"
        # and "httpUrl" are explicitly documented as unsupported legacy
        # field names (antigravity.google/docs/cli/mcp), confirmed directly
        # against the real CLI: `agy plugin validate` rejected a translated
        # "url" field with "must have either command or serverUrl".
        # Renamed only for entries that actually have "url" — stdio entries
        # (which use "command") pass through unchanged.
        if ($hasUrl) {
            $server | Add-Member -MemberType NoteProperty -Name 'serverUrl' -Value $server.url
            $server.PSObject.Properties.Remove('url')
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

function Write-AntigravityPluginJson {
    # Per https://antigravity.google/docs/ide/plugins/ and
    # .../cli/plugins/: a directory is only recognized as a plugin at all
    # if it has a plugin.json marker at its root, with a `name` matching
    # ^[a-zA-Z0-9-_]+$. This repo's own plugins already ship one (linked
    # wholesale); externally-vendored plugins are staged from scratch and
    # need one generated, or Antigravity's loader would not see them.
    param([string]$PluginStagedDir, [string]$PluginName)
    if (-not (Test-Path $PluginStagedDir)) {
        New-Item -ItemType Directory -Path $PluginStagedDir -Force | Out-Null
    }
    $target = Join-Path $PluginStagedDir "plugin.json"
    (@{ name = $PluginName } | ConvertTo-Json) | Set-Content -Path $target
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
    param([string]$RepoRoot, [string]$VendorCacheDir, [string]$CodexSkillsDir, [string]$CodexConfigPath, [string]$CodexAgentsDir, [string]$CodexHooksConfigPath)

    $marketplaces = Get-ExternalMarketplaces -RepoRoot $RepoRoot
    $failures = @()
    $tomlByPlugin = @{}
    $hooksByPlugin = @{}

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

            if (-not (Sync-PluginAgentsCodex -PluginDir $pluginDir -PluginName $pluginName -CodexAgentsDir $CodexAgentsDir)) {
                $failures += "Plugin '$pluginName' (from '$($mp.name)'): one or more agents could not be translated (see messages above)"
            }

            $hooksPath = Join-Path $pluginDir "hooks\hooks.json"
            if (Test-Path $hooksPath) {
                $translated = ConvertTo-CodexHooksJson -HooksJsonPath $hooksPath -PluginDir $pluginDir
                if ($null -eq $translated) {
                    $failures += "Plugin '$pluginName' (from '$($mp.name)'): hook translation to Codex failed"
                } elseif ($translated.Count -gt 0) {
                    $hooksByPlugin[$pluginName] = $translated
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

    if ($hooksByPlugin.Count -gt 0) {
        try {
            Merge-CodexHooksJson -HooksConfigPath $CodexHooksConfigPath -HooksByPlugin $hooksByPlugin
        } catch {
            $failures += "Failed to merge translated hooks into '$CodexHooksConfigPath' — $($_.Exception.Message)"
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
    param([string]$RepoRoot, [string]$VendorCacheDir, [string]$StagedDir, [string]$AntigravityPluginsDir, [string]$AntigravityAgentsDir)

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
                Write-AntigravityPluginJson -PluginStagedDir $stagedPluginDir -PluginName $pluginName

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

                $hooksPath = Join-Path $pluginDir "hooks\hooks.json"
                if (Test-Path $hooksPath) {
                    $hooksJson = ConvertTo-AntigravityHooksJson -HooksJsonPath $hooksPath -PluginDir $pluginDir -PluginName $pluginName
                    if ($null -eq $hooksJson) {
                        $failures += "Plugin '$pluginName' (from '$($mp.name)'): hook translation to Antigravity failed"
                    } elseif ($hooksJson -ne "") {
                        Write-AntigravityHooksJson -PluginStagedDir $stagedPluginDir -JsonContent $hooksJson
                    }
                }

                $finalLink = Join-Path $AntigravityPluginsDir $pluginName
                New-OrRepairJunction -LinkPath $finalLink -TargetPath $stagedPluginDir

                # Agents are not a plugin-level file per Antigravity's own docs
                # (only skills/, mcp_config.json, hooks.json, rules/ are) —
                # they live in a separate global directory, written there
                # directly rather than staged alongside the plugin.
                if (-not (Sync-PluginAgentsAntigravity -PluginDir $pluginDir -PluginName $pluginName -AntigravityAgentsDir $AntigravityAgentsDir)) {
                    $failures += "Plugin '$pluginName' (from '$($mp.name)'): one or more agents could not be translated (see messages above)"
                }
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

# --- claude.ai account surface ---
#
# No upload API exists for account-level Skills, so this module can only
# stage upload-ready bundles and print an ordered checklist against the
# last confirmed state — never touch the account itself.

function Get-AccountManifestJson {
    param([string]$RepoRoot)
    $path = Join-Path $RepoRoot "bootstrap\account-manifest.json"
    if (-not (Test-Path $path)) { return [PSCustomObject]@{ skills = @(); connectors = @() } }
    $json = Get-Content $path -Raw | ConvertFrom-Json
    $skills = if ($json.PSObject.Properties['skills']) { @($json.skills) } else { @() }
    $connectors = if ($json.PSObject.Properties['connectors']) { @($json.connectors) } else { @() }
    return [PSCustomObject]@{ skills = $skills; connectors = $connectors }
}

function Get-AccountLastAppliedJson {
    param([string]$RepoRoot)
    $path = Join-Path $RepoRoot "bootstrap\account-manifest.last-applied.json"
    if (-not (Test-Path $path)) { return [PSCustomObject]@{ skills = @(); connectors = @() } }
    $json = Get-Content $path -Raw | ConvertFrom-Json
    $skills = if ($json.PSObject.Properties['skills']) { @($json.skills) } else { @() }
    $connectors = if ($json.PSObject.Properties['connectors']) { @($json.connectors) } else { @() }
    return [PSCustomObject]@{ skills = $skills; connectors = $connectors }
}

function Copy-AccountSkillBundle {
    param([string]$RepoRoot, [string]$StagedDir, [string]$SkillName, [string]$SourceRelPath)
    $sourceDir = Join-Path $RepoRoot $SourceRelPath
    if (-not (Test-Path $sourceDir)) {
        Write-Host "Account skill '$SkillName': declared source '$SourceRelPath' does not exist"
        return $false
    }
    $destDir = Join-Path $StagedDir $SkillName
    if (Test-Path $destDir) { Remove-Item -Recurse -Force $destDir }
    New-Item -ItemType Directory -Path $destDir -Force | Out-Null
    # A real copy, not a live link — this bundle is meant to be uploaded
    # through the claude.ai UI, which does not resolve links.
    Copy-Item -Path (Join-Path $sourceDir "*") -Destination $destDir -Recurse -Force
    return $true
}

function Write-AccountChecklist {
    param([string]$RepoRoot)
    $current = Get-AccountManifestJson -RepoRoot $RepoRoot
    $lastApplied = Get-AccountLastAppliedJson -RepoRoot $RepoRoot

    $curSkillNames = @($current.skills | ForEach-Object { $_.name })
    $lastSkillNames = @($lastApplied.skills | ForEach-Object { $_.name })
    $curConnectorNames = @($current.connectors | ForEach-Object { $_.name })
    $lastConnectorNames = @($lastApplied.connectors | ForEach-Object { $_.name })

    $addSkills = @($curSkillNames | Where-Object { $_ -notin $lastSkillNames })
    $removeSkills = @($lastSkillNames | Where-Object { $_ -notin $curSkillNames })
    $addConnectors = @($curConnectorNames | Where-Object { $_ -notin $lastConnectorNames })
    $removeConnectors = @($lastConnectorNames | Where-Object { $_ -notin $curConnectorNames })

    $adds = @($addSkills | ForEach-Object { "skill: $_" }) + @($addConnectors | ForEach-Object { "connector: $_" })
    $removes = @($removeSkills | ForEach-Object { "skill: $_" }) + @($removeConnectors | ForEach-Object { "connector: $_" })

    if ($adds.Count -eq 0 -and $removes.Count -eq 0) {
        Write-Host "claude.ai account: up to date with last-applied state."
        return
    }

    Write-Host "claude.ai account checklist (manual — no upload API exists):"
    if ($adds.Count -gt 0) {
        Write-Host "  ADD:"
        $adds | ForEach-Object { Write-Host "    - $_" }
    }
    if ($removes.Count -gt 0) {
        Write-Host "  REMOVE:"
        $removes | ForEach-Object { Write-Host "    - $_" }
    }
    Write-Host "  After applying by hand on claude.ai, run: bootstrap/sync.ps1 -ConfirmAccountApplied"
}

function Sync-AccountBundles {
    param([string]$RepoRoot, [string]$StagedDir)
    $manifest = Get-AccountManifestJson -RepoRoot $RepoRoot
    if (-not (Test-Path $StagedDir)) { New-Item -ItemType Directory -Path $StagedDir -Force | Out-Null }

    $failures = @()
    foreach ($skill in $manifest.skills) {
        if (-not (Copy-AccountSkillBundle -RepoRoot $RepoRoot -StagedDir $StagedDir -SkillName $skill.name -SourceRelPath $skill.source)) {
            $failures += "Account skill '$($skill.name)': declared source '$($skill.source)' does not exist"
        }
    }

    Write-AccountChecklist -RepoRoot $RepoRoot
    return $failures
}

function Confirm-AccountApplied {
    param([string]$RepoRoot)
    $manifestPath = Join-Path $RepoRoot "bootstrap\account-manifest.json"
    $lastAppliedPath = Join-Path $RepoRoot "bootstrap\account-manifest.last-applied.json"
    if (-not (Test-Path $manifestPath)) {
        throw "cannot confirm: '$manifestPath' does not exist"
    }
    Copy-Item -Path $manifestPath -Destination $lastAppliedPath -Force
    Write-Output "Recorded current account-manifest.json as last-applied."
}

# --- Capability detection ---
#
# Each stage is gated on whether its target surface actually exists on this
# machine. A capability test returns a hashtable {Available, Reason}.
# Invoke-Stage reports a skip (with reason) rather than the stage failing
# or silently doing nothing. This is the "stage" interface later specs
# register new stages against: one capability test, one run scriptblock,
# one Invoke-Stage call.

function Test-CodexCapability {
    param([bool]$Overridden)
    if ($Overridden) { return @{ Available = $true } }
    $codexHome = Join-Path $HOME ".codex"
    if (Test-Path $codexHome) { return @{ Available = $true } }
    return @{ Available = $false; Reason = "no '$codexHome' directory found and -CodexSkillsDir was not explicitly set" }
}

function Test-AntigravityCapability {
    param([bool]$Overridden)
    if ($Overridden) { return @{ Available = $true } }
    $geminiHome = Join-Path $HOME ".gemini"
    if (Test-Path $geminiHome) { return @{ Available = $true } }
    return @{ Available = $false; Reason = "no '$geminiHome' directory found and -AntigravityPluginsDir was not explicitly set" }
}

function Test-VendorCacheCapability {
    param([bool]$CodexAvailable, [bool]$AntigravityAvailable)
    if ($CodexAvailable -or $AntigravityAvailable) { return @{ Available = $true } }
    return @{ Available = $false; Reason = "neither Codex nor Antigravity capability detected" }
}

function Test-ClaudeCodeCapability {
    param([bool]$SkipClaudeCode)
    if ($SkipClaudeCode) { return @{ Available = $false; Reason = "-SkipClaudeCode is set" } }
    if (Get-Command claude -ErrorAction SilentlyContinue) { return @{ Available = $true } }
    return @{ Available = $false; Reason = "'claude' CLI not found on PATH" }
}

# --- Stage runner ---
#
# $RunFn must return an array of failure-message strings (empty on
# success). Own-plugin stages (Sync-CodexSkills, Sync-AntigravityPlugins)
# throw on error instead of returning failures — Invoke-Stage catches that
# and converts it into a recorded failure so one bad stage no longer halts
# every later stage, matching the partial-failure isolation the external
# stages already had.

$script:stageOk = @()
$script:stageSkipped = @()
$script:stageFailed = @()
$script:allFailures = @()

function Invoke-Stage {
    param(
        [string]$StageName,
        [hashtable]$Capability,
        [scriptblock]$RunFn
    )
    if (-not $Capability.Available) {
        Write-Host "Skipping stage '$StageName': $($Capability.Reason)"
        $script:stageSkipped += "$StageName`: $($Capability.Reason)"
        return
    }
    $failures = @()
    try {
        $failures = @(& $RunFn)
    } catch {
        $failures = @($_.Exception.Message)
    }
    if ($failures.Count -gt 0) {
        $script:allFailures += $failures
        $script:stageFailed += $StageName
    } else {
        $script:stageOk += $StageName
    }
}

if ($Import) { return }

if ($ConfirmAccountApplied) {
    Confirm-AccountApplied -RepoRoot $RepoRoot
    exit 0
}

$vendorCacheDir = Join-Path $RepoRoot ".vendor-cache"
$stagedDir = Join-Path $vendorCacheDir "_staged"
$codexConfigPath = Join-Path $HOME ".codex\config.toml"
$codexHooksConfigPath = Join-Path $HOME ".codex\hooks.json"

$codexCap = Test-CodexCapability -Overridden $CodexSkillsDirOverridden
$antigravityCap = Test-AntigravityCapability -Overridden $AntigravityPluginsDirOverridden
$vendorCacheCap = Test-VendorCacheCapability -CodexAvailable $codexCap.Available -AntigravityAvailable $antigravityCap.Available
$claudeCodeCap = Test-ClaudeCodeCapability -SkipClaudeCode $SkipClaudeCode.IsPresent

Invoke-Stage -StageName "codex-skills" -Capability $codexCap -RunFn {
    Sync-CodexSkills -RepoRoot $RepoRoot -CodexSkillsDir $CodexSkillsDir | Out-Null
    @()
}
Invoke-Stage -StageName "antigravity-plugins" -Capability $antigravityCap -RunFn {
    Sync-AntigravityPlugins -RepoRoot $RepoRoot -AntigravityPluginsDir $AntigravityPluginsDir | Out-Null
    @()
}

Invoke-Stage -StageName "vendor-cache" -Capability $vendorCacheCap -RunFn {
    Sync-VendorCache -RepoRoot $RepoRoot -VendorCacheDir $vendorCacheDir
}
Invoke-Stage -StageName "command-gap-report" -Capability $vendorCacheCap -RunFn {
    New-CommandGapReport -RepoRoot $RepoRoot -VendorCacheDir $vendorCacheDir
    @()
}
Invoke-Stage -StageName "external-codex-content" -Capability $codexCap -RunFn {
    Sync-ExternalCodexContent -RepoRoot $RepoRoot -VendorCacheDir $vendorCacheDir -CodexSkillsDir $CodexSkillsDir -CodexConfigPath $codexConfigPath -CodexAgentsDir $CodexAgentsDir -CodexHooksConfigPath $codexHooksConfigPath
}
Invoke-Stage -StageName "external-antigravity-content" -Capability $antigravityCap -RunFn {
    Sync-ExternalAntigravityContent -RepoRoot $RepoRoot -VendorCacheDir $vendorCacheDir -StagedDir $stagedDir -AntigravityPluginsDir $AntigravityPluginsDir -AntigravityAgentsDir $AntigravityAgentsDir
}

Invoke-Stage -StageName "claude-code-marketplace" -Capability $claudeCodeCap -RunFn {
    Sync-ClaudeCodeMarketplace -RepoRoot $RepoRoot
}
Invoke-Stage -StageName "account-bundles" -Capability @{ Available = $true } -RunFn {
    Sync-AccountBundles -RepoRoot $RepoRoot -StagedDir (Join-Path $vendorCacheDir "_staged\claude-ai")
}

Write-Output ""
Write-Output "--- Sync summary ---"
if ($script:stageOk.Count -gt 0) {
    Write-Output "Ran: $($script:stageOk -join ', ')"
}
if ($script:stageSkipped.Count -gt 0) {
    Write-Output "Skipped:"
    $script:stageSkipped | ForEach-Object { Write-Output "  - $_" }
}
if ($script:stageFailed.Count -gt 0) {
    Write-Output "Failed: $($script:stageFailed -join ', ')"
}

if ($script:allFailures.Count -gt 0) {
    Write-Output "Sync completed with failures:"
    $script:allFailures | ForEach-Object { Write-Output "  - $_" }
    exit 1
}

Write-Output "Sync complete."
