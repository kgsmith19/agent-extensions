# Full-plugin support (Spec 1) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reference the two external Claude Code marketplaces backing Kyle's real 39-plugin roster (`claude-plugins-official`, `superpowers-marketplace`), and port Skills + MCP servers from all 39 plugins across Claude Code, Codex, and Antigravity.

**Architecture:** A new `bootstrap/external-marketplaces.json` declares the marketplaces and pinned commits. `sync.ps1`/`sync.sh` gain a vendor-cache stage that clones each marketplace to a pinned commit under `.vendor-cache/`, then extend the existing Codex/Antigravity linking loops and Claude Code's native install to also cover that content, translating each plugin's `.mcp.json` into Codex's `~/.codex/config.toml` and Antigravity's `mcp_config.json`.

**Tech Stack:** PowerShell 7+ (native `ConvertFrom-Json`/`ConvertTo-Json`), Bash + `jq` (new prerequisite, bash-only, MCP-translation path only), `git`.

## Global Constraints

- No absolute local paths committed anywhere in this repo — every path is derived at run time from `$HOME`/`$env:USERPROFILE`, matching the existing v1 discipline.
- A single plugin's failure (clone error, malformed `.mcp.json`, unrecognized MCP field) must never abort processing of the other plugins — every sync function collects failures, continues, and the caller reports all of them; the overall script exits non-zero if any occurred.
- No silent fallbacks or silently-dropped capabilities: an MCP server that fails to translate is a reported failure, never a quietly-missing config entry.
- Merges into `~/.codex/config.toml` must preserve any pre-existing unrelated content — never overwrite the whole file.
- `sync.sh`'s MCP-translation path requires `jq` on `PATH`; if absent, fail loudly with an actionable message naming `jq` specifically. The rest of `sync.sh` has no new dependency. `sync.ps1` has no new dependency at all (native JSON cmdlets).
- Junction/symlink safety: never overwrite a path that exists and isn't a link this script manages (existing `New-OrRepairJunction`/`new_or_repair_symlink` rule, unchanged, reused as-is by every new call site).
- TDD first for every task with executable behavior: write the failing test, watch it fail, then implement. Task 1 is data-only (a JSON file with no behavior of its own) — its correctness is verified by Task 3's parser tests, which parse the real file, not a placeholder assertion.
- Follow existing test conventions exactly: plain accumulate-into-`$failures`-array-then-exit scripts (PowerShell) / accumulate-into-`failures=()`-then-exit scripts (Bash), scratch-isolated via a temp directory unless the task is explicitly a live-CLI test (matching `sync.claude-code.test.ps1`'s precedent).

---

### Task 1: `external-marketplaces.json` — real declared content

**Files:**
- Create: `bootstrap/external-marketplaces.json`

**Interfaces:**
- Produces: the on-disk schema every later task's parser (`Get-ExternalMarketplaces` / `get_external_marketplaces_json`, Task 3) reads: `{"marketplaces": [{"name": string, "repo": "owner/repo", "pinnedCommit": 40-char hex string, "plugins": [string, ...]}]}`.

This task has no function under test — it is the declared data other tasks' code operates on. Its correctness is verified by Task 3's tests, which parse this exact file.

- [ ] **Step 1: Write the file**

```json
{
  "marketplaces": [
    {
      "name": "claude-plugins-official",
      "repo": "anthropics/claude-plugins-official",
      "pinnedCommit": "cbe94d02bc8ea7375e13b39cc400e17eeabfcbee",
      "plugins": [
        "agent-sdk-dev",
        "atomic-agents",
        "auth0",
        "claude-code-setup",
        "claude-md-management",
        "claude-security",
        "code-review",
        "code-simplifier",
        "commit-commands",
        "explanatory-output-style",
        "feature-dev",
        "figma",
        "frontend-design",
        "github",
        "greptile",
        "hookify",
        "learning-output-style",
        "liquid-skills",
        "mcp-server-dev",
        "mintlify",
        "playground",
        "playwright",
        "plugin-dev",
        "postman",
        "pr-review-toolkit",
        "ralph-loop",
        "security-guidance",
        "session-report",
        "sourcegraph",
        "supabase",
        "superpowers",
        "ui-theme-designer"
      ]
    },
    {
      "name": "superpowers-marketplace",
      "repo": "obra/superpowers-marketplace",
      "pinnedCommit": "1ab7b8eeef707f21565471f11d3782fac3dd1c61",
      "plugins": [
        "claude-session-driver",
        "double-shot-latte",
        "elements-of-style",
        "episodic-memory",
        "private-journal-mcp",
        "superpowers-chrome",
        "superpowers-lab"
      ]
    }
  ]
}
```

- [ ] **Step 2: Sanity-check the JSON parses**

Run: `pwsh -Command "Get-Content bootstrap/external-marketplaces.json -Raw | ConvertFrom-Json | Out-Null; Write-Output 'OK'"`
Expected: `OK` (no parse error)

- [ ] **Step 3: Commit**

```bash
git add bootstrap/external-marketplaces.json
git commit -m "Add external-marketplaces.json: declare the 2 marketplaces backing Kyle's 39-plugin roster"
```

---

### Task 2: Local fixture marketplace (shared test infrastructure)

**Files:**
- Create: `bootstrap/tests/fixtures/marketplace-fixture.ps1`
- Create: `bootstrap/tests/fixtures/marketplace-fixture.sh`
- Test: `bootstrap/tests/fixtures/marketplace-fixture.test.ps1`
- Test: `bootstrap/tests/fixtures/marketplace-fixture.test.sh`

**Interfaces:**
- Produces: `New-FixtureMarketplace -DestDir <path>` (PowerShell function, returns the resulting commit SHA as a string) and `new_fixture_marketplace <dest_dir>` (Bash function, prints the resulting commit SHA to stdout). Both build a local git repo at `DestDir`/`dest_dir` containing 4 fixture plugins:
  - `alpha-skills/skills/greet/SKILL.md` — a skill, no `.mcp.json` (the zero-MCP-servers case)
  - `beta-mcp-stdio/.mcp.json` — one stdio-transport MCP server
  - `gamma-mcp-http/.mcp.json` — one HTTP-transport MCP server
  - `delta-malformed/.mcp.json` — one MCP server with neither `command` nor `url` (the malformed case)
- Consumes: `git` on `PATH`.

Every later task that needs "an external marketplace" without hitting live GitHub calls this fixture builder.

- [ ] **Step 1: Write the failing test (PowerShell)**

```powershell
# bootstrap/tests/fixtures/marketplace-fixture.test.ps1
#!/usr/bin/env pwsh
$ErrorActionPreference = "Stop"
$here = $PSScriptRoot
. (Join-Path $here "marketplace-fixture.ps1")

$scratch = Join-Path ([System.IO.Path]::GetTempPath()) "agent-extensions-fixturetest-$(Get-Random)"
$sha = New-FixtureMarketplace -DestDir $scratch

$failures = @()

if ($sha -notmatch '^[0-9a-f]{40}$') {
    $failures += "New-FixtureMarketplace did not return a 40-char commit SHA, got: '$sha'"
}

Push-Location $scratch
try {
    $actualSha = (& git rev-parse HEAD).Trim()
    if ($actualSha -ne $sha) {
        $failures += "Returned SHA '$sha' does not match repo HEAD '$actualSha'"
    }
} finally {
    Pop-Location
}

foreach ($expected in @(
    "alpha-skills\skills\greet\SKILL.md",
    "beta-mcp-stdio\.mcp.json",
    "gamma-mcp-http\.mcp.json",
    "delta-malformed\.mcp.json"
)) {
    if (-not (Test-Path (Join-Path $scratch $expected))) {
        $failures += "Expected fixture file missing: $expected"
    }
}

Remove-Item -Recurse -Force $scratch

if ($failures.Count -gt 0) {
    Write-Output "FAIL: marketplace-fixture builder"
    $failures | ForEach-Object { Write-Output "  - $_" }
    exit 1
}

Write-Output "PASS: marketplace-fixture builder produces expected content and commit"
exit 0
```

- [ ] **Step 2: Run it to verify it fails**

Run: `pwsh bootstrap/tests/fixtures/marketplace-fixture.test.ps1`
Expected: FAIL — dot-sourcing `marketplace-fixture.ps1` errors because the file doesn't exist yet.

- [ ] **Step 3: Implement `marketplace-fixture.ps1`**

```powershell
# bootstrap/tests/fixtures/marketplace-fixture.ps1
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pwsh bootstrap/tests/fixtures/marketplace-fixture.test.ps1`
Expected: `PASS: marketplace-fixture builder produces expected content and commit`

- [ ] **Step 5: Write the failing test (Bash)**

```bash
# bootstrap/tests/fixtures/marketplace-fixture.test.sh
#!/usr/bin/env bash
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=/dev/null
source "$HERE/marketplace-fixture.sh"

SCRATCH="$(mktemp -d)"
SHA="$(new_fixture_marketplace "$SCRATCH")"

failures=()

if [[ ! "$SHA" =~ ^[0-9a-f]{40}$ ]]; then
  failures+=("new_fixture_marketplace did not return a 40-char commit SHA, got: '$SHA'")
fi

ACTUAL_SHA="$(git -C "$SCRATCH" rev-parse HEAD)"
if [ "$ACTUAL_SHA" != "$SHA" ]; then
  failures+=("Returned SHA '$SHA' does not match repo HEAD '$ACTUAL_SHA'")
fi

for expected in \
  "alpha-skills/skills/greet/SKILL.md" \
  "beta-mcp-stdio/.mcp.json" \
  "gamma-mcp-http/.mcp.json" \
  "delta-malformed/.mcp.json"; do
  if [ ! -f "$SCRATCH/$expected" ]; then
    failures+=("Expected fixture file missing: $expected")
  fi
done

rm -rf "$SCRATCH"

if [ ${#failures[@]} -gt 0 ]; then
  echo "FAIL: marketplace-fixture builder"
  printf '  - %s\n' "${failures[@]}"
  exit 1
fi

echo "PASS: marketplace-fixture builder produces expected content and commit"
exit 0
```

- [ ] **Step 6: Run it to verify it fails**

Run: `bash bootstrap/tests/fixtures/marketplace-fixture.test.sh`
Expected: FAIL — sourcing `marketplace-fixture.sh` errors because the file doesn't exist yet.

- [ ] **Step 7: Implement `marketplace-fixture.sh`**

```bash
# bootstrap/tests/fixtures/marketplace-fixture.sh
#!/usr/bin/env bash
# Builds a local git repo standing in for an external marketplace, with 4
# fixture plugins covering: skill-only (zero MCP servers), stdio MCP,
# HTTP MCP, and a malformed MCP server (neither command nor url).
new_fixture_marketplace() {
  local dest_dir="$1"

  rm -rf "$dest_dir"
  mkdir -p "$dest_dir"

  mkdir -p "$dest_dir/alpha-skills/skills/greet"
  cat > "$dest_dir/alpha-skills/skills/greet/SKILL.md" <<'EOF'
---
name: greet
description: Says hello. Fixture skill for agent-extensions sync tests.
---

Say hello to the user.
EOF

  mkdir -p "$dest_dir/beta-mcp-stdio"
  cat > "$dest_dir/beta-mcp-stdio/.mcp.json" <<'EOF'
{
  "mcpServers": {
    "fixture-stdio": {
      "command": "node",
      "args": ["server.js", "--port", "0"],
      "env": { "FIXTURE_MODE": "stdio" }
    }
  }
}
EOF

  mkdir -p "$dest_dir/gamma-mcp-http"
  cat > "$dest_dir/gamma-mcp-http/.mcp.json" <<'EOF'
{
  "mcpServers": {
    "fixture-http": {
      "url": "https://fixture.example.com/mcp",
      "headers": { "Authorization": "Bearer FIXTURE_TOKEN" }
    }
  }
}
EOF

  mkdir -p "$dest_dir/delta-malformed"
  cat > "$dest_dir/delta-malformed/.mcp.json" <<'EOF'
{
  "mcpServers": {
    "fixture-bad": {
      "transportKind": "carrier-pigeon"
    }
  }
}
EOF

  (
    cd "$dest_dir"
    git init -q
    git config user.email "fixture@agent-extensions.test"
    git config user.name "agent-extensions fixture"
    git add -A
    git commit -q -m "Fixture marketplace content"
    git rev-parse HEAD
  )
}
```

- [ ] **Step 8: Run test to verify it passes**

Run: `bash bootstrap/tests/fixtures/marketplace-fixture.test.sh`
Expected: `PASS: marketplace-fixture builder produces expected content and commit`

- [ ] **Step 9: Commit**

```bash
git add bootstrap/tests/fixtures/
git commit -m "Add local fixture-marketplace builder for scratch-isolated sync tests"
```

---

### Task 3: Vendor-cache clone/pin mechanism

**Files:**
- Modify: `bootstrap/sync.ps1`
- Modify: `bootstrap/sync.sh`
- Create: `bootstrap/vendor-cache.test.ps1`
- Create: `bootstrap/vendor-cache.test.sh`

**Interfaces:**
- Consumes: `New-FixtureMarketplace -DestDir <path>` / `new_fixture_marketplace <dest_dir>` (Task 2).
- Produces:
  - `Get-ExternalMarketplaces -RepoRoot <path>` → array of `[PSCustomObject]` with `.name`, `.repo`, `.pinnedCommit`, `.plugins` (array of strings). Empty array if `bootstrap/external-marketplaces.json` doesn't exist.
  - `Resolve-MarketplaceUrl -Repo <string>` → a clone URL. `owner/repo` shorthand (matching `^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$`) resolves to `https://github.com/owner/repo.git`; anything else (a full URL or local path) is returned unchanged.
  - `Sync-VendorCache -RepoRoot <path> -VendorCacheDir <path>` → array of failure-message strings (empty on full success). Clones each declared marketplace to `VendorCacheDir/<name>`, checked out at `pinnedCommit`; a no-op if already at that commit.
  - Bash mirrors: `get_external_marketplaces_json <repo_root>` (prints one compact JSON object per marketplace, one per line), `resolve_marketplace_url <repo>`, `sync_vendor_cache <repo_root> <vendor_cache_dir>` (prints failures to stderr, returns 0 on full success, 1 if any marketplace failed).

- [ ] **Step 1: Write the failing test (PowerShell)**

```powershell
# bootstrap/vendor-cache.test.ps1
#!/usr/bin/env pwsh
$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $PSScriptRoot
. (Join-Path $PSScriptRoot "sync.ps1") -Import
. (Join-Path $RepoRoot "bootstrap\tests\fixtures\marketplace-fixture.ps1")

$scratch = Join-Path ([System.IO.Path]::GetTempPath()) "agent-extensions-vendorcache-$(Get-Random)"
$fixtureRepo = Join-Path $scratch "fixture-marketplace"
$declareRoot = Join-Path $scratch "declare-root"
$vendorCache = Join-Path $scratch "vendor-cache"
New-Item -ItemType Directory -Path $declareRoot -Force | Out-Null

$sha = New-FixtureMarketplace -DestDir $fixtureRepo

$manifest = @{
    marketplaces = @(
        @{ name = "fixture-mp"; repo = $fixtureRepo; pinnedCommit = $sha; plugins = @("alpha-skills") }
    )
} | ConvertTo-Json -Depth 10
$bootstrapDir = Join-Path $declareRoot "bootstrap"
New-Item -ItemType Directory -Path $bootstrapDir -Force | Out-Null
Set-Content -Path (Join-Path $bootstrapDir "external-marketplaces.json") -Value $manifest

$failures = @()

# --- Resolve-MarketplaceUrl ---
if ((Resolve-MarketplaceUrl -Repo "anthropics/claude-plugins-official") -ne "https://github.com/anthropics/claude-plugins-official.git") {
    $failures += "Resolve-MarketplaceUrl did not expand owner/repo shorthand correctly"
}
if ((Resolve-MarketplaceUrl -Repo $fixtureRepo) -ne $fixtureRepo) {
    $failures += "Resolve-MarketplaceUrl should pass through a local path unchanged"
}

# --- Get-ExternalMarketplaces ---
$parsed = Get-ExternalMarketplaces -RepoRoot $declareRoot
if ($parsed.Count -ne 1 -or $parsed[0].name -ne "fixture-mp") {
    $failures += "Get-ExternalMarketplaces did not parse the declared fixture marketplace"
}

# --- Sync-VendorCache: initial clone ---
$cloneFailures = Sync-VendorCache -RepoRoot $declareRoot -VendorCacheDir $vendorCache
if ($cloneFailures.Count -gt 0) {
    $failures += "Sync-VendorCache reported failures on a valid fixture: $($cloneFailures -join '; ')"
}
$clonedFile = Join-Path $vendorCache "fixture-mp\alpha-skills\skills\greet\SKILL.md"
if (-not (Test-Path $clonedFile)) {
    $failures += "Vendor cache clone did not produce expected file: $clonedFile"
}

# --- Idempotency: mark the clone, re-run, confirm no re-clone ---
$marker = Join-Path $vendorCache "fixture-mp\MARKER.txt"
Set-Content -Path $marker -Value "should survive a no-op re-sync"
Sync-VendorCache -RepoRoot $declareRoot -VendorCacheDir $vendorCache | Out-Null
if (-not (Test-Path $marker)) {
    $failures += "Sync-VendorCache re-cloned an already-pinned marketplace (marker file was wiped)"
}

# --- Missing pinned commit: loud, reported failure, not silent ---
$badManifest = @{
    marketplaces = @(
        @{ name = "fixture-mp"; repo = $fixtureRepo; pinnedCommit = "0000000000000000000000000000000000000bad"; plugins = @("alpha-skills") }
    )
} | ConvertTo-Json -Depth 10
Set-Content -Path (Join-Path $bootstrapDir "external-marketplaces.json") -Value $badManifest
$badVendorCache = Join-Path $scratch "vendor-cache-bad"
$badFailures = Sync-VendorCache -RepoRoot $declareRoot -VendorCacheDir $badVendorCache
if ($badFailures.Count -eq 0) {
    $failures += "Sync-VendorCache silently succeeded on a nonexistent pinned commit"
}

Remove-Item -Recurse -Force $scratch

if ($failures.Count -gt 0) {
    Write-Output "FAIL: vendor-cache clone/pin"
    $failures | ForEach-Object { Write-Output "  - $_" }
    exit 1
}

Write-Output "PASS: vendor-cache clone/pin, idempotent, loud on bad pin"
exit 0
```

- [ ] **Step 2: Run it to verify it fails**

Run: `pwsh bootstrap/vendor-cache.test.ps1`
Expected: FAIL — `Resolve-MarketplaceUrl`/`Get-ExternalMarketplaces`/`Sync-VendorCache` are not defined yet.

- [ ] **Step 3: Implement in `sync.ps1`**

Add these three functions to `bootstrap/sync.ps1`, after the existing `New-OrRepairJunction` function and before `Sync-CodexSkills`:

```powershell
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

        if (Test-Path $dest) { Remove-Item $dest -Recurse -Force }
        New-Item -ItemType Directory -Path $dest -Force | Out-Null

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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pwsh bootstrap/vendor-cache.test.ps1`
Expected: `PASS: vendor-cache clone/pin, idempotent, loud on bad pin`

- [ ] **Step 5: Write the failing test (Bash)**

```bash
# bootstrap/vendor-cache.test.sh
#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=/dev/null
source "$REPO_ROOT/bootstrap/sync.sh" --import
# shellcheck source=/dev/null
source "$REPO_ROOT/bootstrap/tests/fixtures/marketplace-fixture.sh"

SCRATCH="$(mktemp -d)"
FIXTURE_REPO="$SCRATCH/fixture-marketplace"
DECLARE_ROOT="$SCRATCH/declare-root"
VENDOR_CACHE="$SCRATCH/vendor-cache"
mkdir -p "$DECLARE_ROOT/bootstrap"

SHA="$(new_fixture_marketplace "$FIXTURE_REPO")"

cat > "$DECLARE_ROOT/bootstrap/external-marketplaces.json" <<EOF
{
  "marketplaces": [
    { "name": "fixture-mp", "repo": "$FIXTURE_REPO", "pinnedCommit": "$SHA", "plugins": ["alpha-skills"] }
  ]
}
EOF

failures=()

# --- resolve_marketplace_url ---
if [ "$(resolve_marketplace_url "anthropics/claude-plugins-official")" != "https://github.com/anthropics/claude-plugins-official.git" ]; then
  failures+=("resolve_marketplace_url did not expand owner/repo shorthand correctly")
fi
if [ "$(resolve_marketplace_url "$FIXTURE_REPO")" != "$FIXTURE_REPO" ]; then
  failures+=("resolve_marketplace_url should pass through a local path unchanged")
fi

# --- get_external_marketplaces_json ---
COUNT="$(get_external_marketplaces_json "$DECLARE_ROOT" | wc -l | tr -d ' ')"
if [ "$COUNT" != "1" ]; then
  failures+=("get_external_marketplaces_json did not parse exactly 1 declared marketplace, got $COUNT")
fi

# --- sync_vendor_cache: initial clone ---
if ! sync_vendor_cache "$DECLARE_ROOT" "$VENDOR_CACHE"; then
  failures+=("sync_vendor_cache reported failure on a valid fixture")
fi
CLONED_FILE="$VENDOR_CACHE/fixture-mp/alpha-skills/skills/greet/SKILL.md"
if [ ! -f "$CLONED_FILE" ]; then
  failures+=("Vendor cache clone did not produce expected file: $CLONED_FILE")
fi

# --- Idempotency ---
MARKER="$VENDOR_CACHE/fixture-mp/MARKER.txt"
echo "should survive a no-op re-sync" > "$MARKER"
sync_vendor_cache "$DECLARE_ROOT" "$VENDOR_CACHE" || true
if [ ! -f "$MARKER" ]; then
  failures+=("sync_vendor_cache re-cloned an already-pinned marketplace (marker file was wiped)")
fi

# --- Missing pinned commit: loud, reported failure ---
cat > "$DECLARE_ROOT/bootstrap/external-marketplaces.json" <<EOF
{
  "marketplaces": [
    { "name": "fixture-mp", "repo": "$FIXTURE_REPO", "pinnedCommit": "0000000000000000000000000000000000000bad", "plugins": ["alpha-skills"] }
  ]
}
EOF
BAD_VENDOR_CACHE="$SCRATCH/vendor-cache-bad"
if sync_vendor_cache "$DECLARE_ROOT" "$BAD_VENDOR_CACHE" 2>/dev/null; then
  failures+=("sync_vendor_cache silently succeeded on a nonexistent pinned commit")
fi

rm -rf "$SCRATCH"

if [ ${#failures[@]} -gt 0 ]; then
  echo "FAIL: vendor-cache clone/pin"
  printf '  - %s\n' "${failures[@]}"
  exit 1
fi

echo "PASS: vendor-cache clone/pin, idempotent, loud on bad pin"
exit 0
```

- [ ] **Step 6: Run it to verify it fails**

Run: `bash bootstrap/vendor-cache.test.sh`
Expected: FAIL — `resolve_marketplace_url`/`get_external_marketplaces_json`/`sync_vendor_cache` are not defined yet.

- [ ] **Step 7: Implement in `sync.sh`**

Add these three functions to `bootstrap/sync.sh`, after the existing `new_or_repair_symlink` function and before `sync_codex_skills`:

```bash
get_external_marketplaces_json() {
  local repo_root="$1"
  local path="$repo_root/bootstrap/external-marketplaces.json"
  [ -f "$path" ] || return 0
  if ! command -v jq >/dev/null 2>&1; then
    echo "jq is required to read external-marketplaces.json but was not found on PATH." >&2
    return 1
  fi
  jq -c '.marketplaces[]?' "$path"
}

resolve_marketplace_url() {
  local repo="$1"
  if [[ "$repo" =~ ^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$ ]]; then
    echo "https://github.com/$repo.git"
  else
    echo "$repo"
  fi
}

sync_vendor_cache() {
  local repo_root="$1"
  local vendor_cache_dir="$2"

  if ! command -v jq >/dev/null 2>&1; then
    echo "jq is required for external-marketplace sync but was not found on PATH." >&2
    return 1
  fi

  local failed=0
  local mp_json name repo pinned_commit dest current_sha url
  while IFS= read -r mp_json; do
    [ -n "$mp_json" ] || continue
    name="$(echo "$mp_json" | jq -r '.name')"
    repo="$(echo "$mp_json" | jq -r '.repo')"
    pinned_commit="$(echo "$mp_json" | jq -r '.pinnedCommit')"
    dest="$vendor_cache_dir/$name"

    current_sha=""
    if [ -d "$dest/.git" ]; then
      current_sha="$(git -C "$dest" rev-parse HEAD 2>/dev/null || true)"
    fi
    if [ "$current_sha" = "$pinned_commit" ]; then
      continue
    fi

    rm -rf "$dest"
    mkdir -p "$dest"
    url="$(resolve_marketplace_url "$repo")"

    if ! git -C "$dest" init -q \
        || ! git -C "$dest" remote add origin "$url" \
        || ! git -C "$dest" fetch --depth 1 origin "$pinned_commit" 2>/dev/null; then
      echo "Marketplace '$name': failed to fetch commit '$pinned_commit' from '$url' — it may no longer exist upstream." >&2
      failed=1
      continue
    fi
    if ! git -C "$dest" checkout -q FETCH_HEAD; then
      echo "Marketplace '$name': failed to check out pinned commit '$pinned_commit'." >&2
      failed=1
    fi
  done < <(get_external_marketplaces_json "$repo_root")

  return $failed
}
```

- [ ] **Step 8: Run test to verify it passes**

Run: `bash bootstrap/vendor-cache.test.sh`
Expected: `PASS: vendor-cache clone/pin, idempotent, loud on bad pin`

- [ ] **Step 9: Commit**

```bash
git add bootstrap/sync.ps1 bootstrap/sync.sh bootstrap/vendor-cache.test.ps1 bootstrap/vendor-cache.test.sh
git commit -m "Add vendor-cache clone/pin mechanism for external marketplaces"
```

---

### Task 4: MCP-to-Codex-TOML translator

**Files:**
- Modify: `bootstrap/sync.ps1`
- Modify: `bootstrap/sync.sh`
- Create: `bootstrap/mcp-translate.test.ps1`
- Create: `bootstrap/mcp-translate.test.sh`

**Interfaces:**
- Produces: `ConvertTo-CodexMcpToml -McpServers <PSCustomObject>` → TOML text (string). Input is the parsed `.mcpServers` object from a plugin's `.mcp.json` (each property name is a server name; each value is a server definition object). Recognized shape: **stdio** servers have `command` (required), optionally `args` (array of strings), `env` (object of string→string); **http** servers have `url` (required), optionally `headers` (object of string→string). A server with neither `command` nor `url`, with both, or with any property outside the recognized set for its detected transport throws `System.Exception` with a message naming the server and the problem. An empty `$McpServers` (no properties) returns `""`.
- Bash mirror: `mcp_json_to_codex_toml <mcp_servers_json>` — argument is a compact JSON string (the `.mcpServers` object). Prints TOML to stdout on success (exit 0); prints an error to stderr and returns 1 on any unrecognized shape. Requires `jq` on `PATH` (already required by `sync_vendor_cache`, Task 3).

- [ ] **Step 1: Write the failing test (PowerShell)**

```powershell
# bootstrap/mcp-translate.test.ps1
#!/usr/bin/env pwsh
$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "sync.ps1") -Import

$failures = @()

# --- stdio server ---
$stdio = @'
{ "fixture-stdio": { "command": "node", "args": ["server.js", "--port", "0"], "env": { "FIXTURE_MODE": "stdio" } } }
'@ | ConvertFrom-Json
$stdioToml = ConvertTo-CodexMcpToml -McpServers $stdio
if ($stdioToml -notmatch '\[mcp_servers\.fixture-stdio\]') {
    $failures += "stdio: missing table header"
}
if ($stdioToml -notmatch 'command = "node"') {
    $failures += "stdio: missing/incorrect command"
}
if ($stdioToml -notmatch 'args = \["server\.js", "--port", "0"\]') {
    $failures += "stdio: missing/incorrect args"
}
if ($stdioToml -notmatch 'env = \{ FIXTURE_MODE = "stdio" \}') {
    $failures += "stdio: missing/incorrect env"
}

# --- http server ---
$http = @'
{ "fixture-http": { "url": "https://fixture.example.com/mcp", "headers": { "Authorization": "Bearer FIXTURE_TOKEN" } } }
'@ | ConvertFrom-Json
$httpToml = ConvertTo-CodexMcpToml -McpServers $http
if ($httpToml -notmatch '\[mcp_servers\.fixture-http\]') {
    $failures += "http: missing table header"
}
if ($httpToml -notmatch 'url = "https://fixture\.example\.com/mcp"') {
    $failures += "http: missing/incorrect url"
}
if ($httpToml -notmatch 'http_headers = \{ Authorization = "Bearer FIXTURE_TOKEN" \}') {
    $failures += "http: missing/incorrect headers"
}

# --- zero servers ---
$empty = [PSCustomObject]@{}
if ((ConvertTo-CodexMcpToml -McpServers $empty) -ne "") {
    $failures += "empty: expected empty string for zero MCP servers"
}

# --- malformed: neither command nor url ---
$bad = @'
{ "fixture-bad": { "transportKind": "carrier-pigeon" } }
'@ | ConvertFrom-Json
$threw = $false
try { ConvertTo-CodexMcpToml -McpServers $bad | Out-Null } catch { $threw = $true }
if (-not $threw) {
    $failures += "malformed: expected ConvertTo-CodexMcpToml to throw, it did not"
}

if ($failures.Count -gt 0) {
    Write-Output "FAIL: ConvertTo-CodexMcpToml"
    $failures | ForEach-Object { Write-Output "  - $_" }
    exit 1
}

Write-Output "PASS: ConvertTo-CodexMcpToml handles stdio, http, empty, and malformed input"
exit 0
```

- [ ] **Step 2: Run it to verify it fails**

Run: `pwsh bootstrap/mcp-translate.test.ps1`
Expected: FAIL — `ConvertTo-CodexMcpToml` is not defined yet.

- [ ] **Step 3: Implement in `sync.ps1`**

Add these two functions, after `Sync-VendorCache` and before `Sync-CodexSkills`:

```powershell
function ConvertTo-TomlString {
    param([string]$Value)
    $escaped = $Value -replace '\\', '\\\\' -replace '"', '\"'
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
```

Note: the function returns `""` for zero servers because `($lines -join "`n").Trim()` on an empty `$lines` array yields `""`.

- [ ] **Step 4: Run test to verify it passes**

Run: `pwsh bootstrap/mcp-translate.test.ps1`
Expected: `PASS: ConvertTo-CodexMcpToml handles stdio, http, empty, and malformed input`

- [ ] **Step 5: Write the failing test (Bash)**

```bash
# bootstrap/mcp-translate.test.sh
#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=/dev/null
source "$REPO_ROOT/bootstrap/sync.sh" --import

failures=()

STDIO_JSON='{ "fixture-stdio": { "command": "node", "args": ["server.js", "--port", "0"], "env": { "FIXTURE_MODE": "stdio" } } }'
STDIO_TOML="$(mcp_json_to_codex_toml "$STDIO_JSON")"
echo "$STDIO_TOML" | grep -qF '[mcp_servers.fixture-stdio]' || failures+=("stdio: missing table header")
echo "$STDIO_TOML" | grep -qF 'command = "node"' || failures+=("stdio: missing/incorrect command")
echo "$STDIO_TOML" | grep -qF 'args = ["server.js","--port","0"]' || failures+=("stdio: missing/incorrect args")
echo "$STDIO_TOML" | grep -qF 'env = { FIXTURE_MODE = "stdio" }' || failures+=("stdio: missing/incorrect env")

HTTP_JSON='{ "fixture-http": { "url": "https://fixture.example.com/mcp", "headers": { "Authorization": "Bearer FIXTURE_TOKEN" } } }'
HTTP_TOML="$(mcp_json_to_codex_toml "$HTTP_JSON")"
echo "$HTTP_TOML" | grep -qF '[mcp_servers.fixture-http]' || failures+=("http: missing table header")
echo "$HTTP_TOML" | grep -qF 'url = "https://fixture.example.com/mcp"' || failures+=("http: missing/incorrect url")
echo "$HTTP_TOML" | grep -qF 'http_headers = { Authorization = "Bearer FIXTURE_TOKEN" }' || failures+=("http: missing/incorrect headers")

EMPTY_TOML="$(mcp_json_to_codex_toml '{}')"
if [ -n "$EMPTY_TOML" ]; then
  failures+=("empty: expected empty output for zero MCP servers")
fi

BAD_JSON='{ "fixture-bad": { "transportKind": "carrier-pigeon" } }'
if mcp_json_to_codex_toml "$BAD_JSON" 2>/dev/null; then
  failures+=("malformed: expected mcp_json_to_codex_toml to fail, it did not")
fi

if [ ${#failures[@]} -gt 0 ]; then
  echo "FAIL: mcp_json_to_codex_toml"
  printf '  - %s\n' "${failures[@]}"
  exit 1
fi

echo "PASS: mcp_json_to_codex_toml handles stdio, http, empty, and malformed input"
exit 0
```

- [ ] **Step 6: Run it to verify it fails**

Run: `bash bootstrap/mcp-translate.test.sh`
Expected: FAIL — `mcp_json_to_codex_toml` is not defined yet.

- [ ] **Step 7: Implement in `sync.sh`**

Add this function, after `sync_vendor_cache` and before `sync_codex_skills`:

```bash
mcp_json_to_codex_toml() {
  local mcp_servers_json="$1"

  local names name server has_command has_url allowed_regex unknown out=""
  names="$(echo "$mcp_servers_json" | jq -r 'keys[]')"

  while IFS= read -r name; do
    [ -n "$name" ] || continue
    server="$(echo "$mcp_servers_json" | jq -c --arg n "$name" '.[$n]')"
    has_command="$(echo "$server" | jq 'has("command")')"
    has_url="$(echo "$server" | jq 'has("url")')"

    if [ "$has_command" = "false" ] && [ "$has_url" = "false" ]; then
      echo "MCP server '$name' has neither 'command' (stdio) nor 'url' (http) — unrecognized server shape." >&2
      return 1
    fi
    if [ "$has_command" = "true" ] && [ "$has_url" = "true" ]; then
      echo "MCP server '$name' has both 'command' and 'url' — ambiguous transport, cannot translate." >&2
      return 1
    fi

    if [ "$has_command" = "true" ]; then
      allowed_regex='^(command|args|env)$'
    else
      allowed_regex='^(url|headers)$'
    fi
    unknown="$(echo "$server" | jq -r --arg re "$allowed_regex" 'keys[] | select(test($re) | not)')"
    if [ -n "$unknown" ]; then
      echo "MCP server '$name' has unrecognized field(s): $(echo "$unknown" | tr '\n' ' ')." >&2
      return 1
    fi

    out+="[mcp_servers.$name]"$'\n'
    if [ "$has_command" = "true" ]; then
      out+="command = $(echo "$server" | jq '.command')"$'\n'
      if echo "$server" | jq -e 'has("args")' >/dev/null; then
        out+="args = $(echo "$server" | jq -c '.args')"$'\n'
      fi
      if echo "$server" | jq -e 'has("env")' >/dev/null; then
        local env_pairs
        env_pairs="$(echo "$server" | jq -r '.env | to_entries | map("\(.key) = \(.value | tojson)") | join(", ")')"
        out+="env = { $env_pairs }"$'\n'
      fi
    else
      out+="url = $(echo "$server" | jq '.url')"$'\n'
      if echo "$server" | jq -e 'has("headers")' >/dev/null; then
        local header_pairs
        header_pairs="$(echo "$server" | jq -r '.headers | to_entries | map("\(.key) = \(.value | tojson)") | join(", ")')"
        out+="http_headers = { $header_pairs }"$'\n'
      fi
    fi
    out+=$'\n'
  done <<< "$names"

  printf '%s' "$out"
}
```

Note: any trailing blank line `out` accumulates is harmless here — every caller captures this function's output via `$(...)` command substitution, which strips trailing newlines automatically (confirmed by this task's own test, which uses `"$(mcp_json_to_codex_toml ...)"` throughout).

- [ ] **Step 8: Run test to verify it passes**

Run: `bash bootstrap/mcp-translate.test.sh`
Expected: `PASS: mcp_json_to_codex_toml handles stdio, http, empty, and malformed input`

- [ ] **Step 9: Commit**

```bash
git add bootstrap/sync.ps1 bootstrap/sync.sh bootstrap/mcp-translate.test.ps1 bootstrap/mcp-translate.test.sh
git commit -m "Add MCP-to-Codex-TOML translator"
```

---

### Task 5: MCP-to-Antigravity-JSON translator

**Files:**
- Modify: `bootstrap/sync.ps1`
- Modify: `bootstrap/sync.sh`
- Create: `bootstrap/mcp-translate-antigravity.test.ps1`
- Create: `bootstrap/mcp-translate-antigravity.test.sh`

**Interfaces:**
- Produces: `ConvertTo-AntigravityMcpConfig -McpServers <PSCustomObject>` → JSON text (string) shaped `{"mcpServers": {...}}`. Same input contract, same strict validation (command-xor-url, allow-listed fields per transport) as `ConvertTo-CodexMcpToml` (Task 4) — this is a structural passthrough/validate, not a reformat, since Antigravity's `mcp_config.json` is assumed (an explicitly unverified assumption, flagged in the design doc's Risks section) to share Claude Code's `.mcp.json` shape. Throws the same way as Task 4 on a malformed server. An empty `$McpServers` returns `{"mcpServers":{}}`.
- Bash mirror: `mcp_json_to_antigravity_config <mcp_servers_json>` — prints the JSON to stdout on success, error to stderr + return 1 on failure.

- [ ] **Step 1: Write the failing test (PowerShell)**

```powershell
# bootstrap/mcp-translate-antigravity.test.ps1
#!/usr/bin/env pwsh
$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "sync.ps1") -Import

$failures = @()

$stdio = @'
{ "fixture-stdio": { "command": "node", "args": ["server.js", "--port", "0"], "env": { "FIXTURE_MODE": "stdio" } } }
'@ | ConvertFrom-Json
$stdioConfig = ConvertTo-AntigravityMcpConfig -McpServers $stdio
$parsedStdio = $stdioConfig | ConvertFrom-Json
if ($parsedStdio.mcpServers.'fixture-stdio'.command -ne "node") {
    $failures += "stdio: command not preserved in Antigravity config"
}
if (($parsedStdio.mcpServers.'fixture-stdio'.args -join ",") -ne "server.js,--port,0") {
    $failures += "stdio: args not preserved in Antigravity config"
}
if ($parsedStdio.mcpServers.'fixture-stdio'.env.FIXTURE_MODE -ne "stdio") {
    $failures += "stdio: env not preserved in Antigravity config"
}

$http = @'
{ "fixture-http": { "url": "https://fixture.example.com/mcp", "headers": { "Authorization": "Bearer FIXTURE_TOKEN" } } }
'@ | ConvertFrom-Json
$httpConfig = ConvertTo-AntigravityMcpConfig -McpServers $http
$parsedHttp = $httpConfig | ConvertFrom-Json
if ($parsedHttp.mcpServers.'fixture-http'.url -ne "https://fixture.example.com/mcp") {
    $failures += "http: url not preserved in Antigravity config"
}
if ($parsedHttp.mcpServers.'fixture-http'.headers.Authorization -ne "Bearer FIXTURE_TOKEN") {
    $failures += "http: headers not preserved in Antigravity config"
}

$empty = [PSCustomObject]@{}
$emptyParsed = (ConvertTo-AntigravityMcpConfig -McpServers $empty) | ConvertFrom-Json
if ($emptyParsed.mcpServers.PSObject.Properties.Count -ne 0) {
    $failures += "empty: expected mcpServers to be an empty object"
}

$bad = @'
{ "fixture-bad": { "transportKind": "carrier-pigeon" } }
'@ | ConvertFrom-Json
$threw = $false
try { ConvertTo-AntigravityMcpConfig -McpServers $bad | Out-Null } catch { $threw = $true }
if (-not $threw) {
    $failures += "malformed: expected ConvertTo-AntigravityMcpConfig to throw, it did not"
}

if ($failures.Count -gt 0) {
    Write-Output "FAIL: ConvertTo-AntigravityMcpConfig"
    $failures | ForEach-Object { Write-Output "  - $_" }
    exit 1
}

Write-Output "PASS: ConvertTo-AntigravityMcpConfig handles stdio, http, empty, and malformed input"
exit 0
```

- [ ] **Step 2: Run it to verify it fails**

Run: `pwsh bootstrap/mcp-translate-antigravity.test.ps1`
Expected: FAIL — `ConvertTo-AntigravityMcpConfig` is not defined yet.

- [ ] **Step 3: Implement in `sync.ps1`**

Add this function, immediately after `ConvertTo-CodexMcpToml`:

```powershell
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pwsh bootstrap/mcp-translate-antigravity.test.ps1`
Expected: `PASS: ConvertTo-AntigravityMcpConfig handles stdio, http, empty, and malformed input`

- [ ] **Step 5: Write the failing test (Bash)**

```bash
# bootstrap/mcp-translate-antigravity.test.sh
#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=/dev/null
source "$REPO_ROOT/bootstrap/sync.sh" --import

failures=()

STDIO_JSON='{ "fixture-stdio": { "command": "node", "args": ["server.js", "--port", "0"], "env": { "FIXTURE_MODE": "stdio" } } }'
STDIO_CONFIG="$(mcp_json_to_antigravity_config "$STDIO_JSON")"
[ "$(echo "$STDIO_CONFIG" | jq -r '.mcpServers."fixture-stdio".command')" = "node" ] || failures+=("stdio: command not preserved")
[ "$(echo "$STDIO_CONFIG" | jq -c '.mcpServers."fixture-stdio".args')" = '["server.js","--port","0"]' ] || failures+=("stdio: args not preserved")
[ "$(echo "$STDIO_CONFIG" | jq -r '.mcpServers."fixture-stdio".env.FIXTURE_MODE')" = "stdio" ] || failures+=("stdio: env not preserved")

HTTP_JSON='{ "fixture-http": { "url": "https://fixture.example.com/mcp", "headers": { "Authorization": "Bearer FIXTURE_TOKEN" } } }'
HTTP_CONFIG="$(mcp_json_to_antigravity_config "$HTTP_JSON")"
[ "$(echo "$HTTP_CONFIG" | jq -r '.mcpServers."fixture-http".url')" = "https://fixture.example.com/mcp" ] || failures+=("http: url not preserved")
[ "$(echo "$HTTP_CONFIG" | jq -r '.mcpServers."fixture-http".headers.Authorization')" = "Bearer FIXTURE_TOKEN" ] || failures+=("http: headers not preserved")

EMPTY_CONFIG="$(mcp_json_to_antigravity_config '{}')"
[ "$(echo "$EMPTY_CONFIG" | jq '.mcpServers | length')" = "0" ] || failures+=("empty: expected mcpServers to be an empty object")

BAD_JSON='{ "fixture-bad": { "transportKind": "carrier-pigeon" } }'
if mcp_json_to_antigravity_config "$BAD_JSON" 2>/dev/null; then
  failures+=("malformed: expected mcp_json_to_antigravity_config to fail, it did not")
fi

if [ ${#failures[@]} -gt 0 ]; then
  echo "FAIL: mcp_json_to_antigravity_config"
  printf '  - %s\n' "${failures[@]}"
  exit 1
fi

echo "PASS: mcp_json_to_antigravity_config handles stdio, http, empty, and malformed input"
exit 0
```

- [ ] **Step 6: Run it to verify it fails**

Run: `bash bootstrap/mcp-translate-antigravity.test.sh`
Expected: FAIL — `mcp_json_to_antigravity_config` is not defined yet.

- [ ] **Step 7: Implement in `sync.sh`**

Add this function, immediately after `mcp_json_to_codex_toml`:

```bash
mcp_json_to_antigravity_config() {
  local mcp_servers_json="$1"

  local names name server has_command has_url allowed_regex unknown
  names="$(echo "$mcp_servers_json" | jq -r 'keys[]')"

  while IFS= read -r name; do
    [ -n "$name" ] || continue
    server="$(echo "$mcp_servers_json" | jq -c --arg n "$name" '.[$n]')"
    has_command="$(echo "$server" | jq 'has("command")')"
    has_url="$(echo "$server" | jq 'has("url")')"

    if [ "$has_command" = "false" ] && [ "$has_url" = "false" ]; then
      echo "MCP server '$name' has neither 'command' (stdio) nor 'url' (http) — unrecognized server shape." >&2
      return 1
    fi
    if [ "$has_command" = "true" ] && [ "$has_url" = "true" ]; then
      echo "MCP server '$name' has both 'command' and 'url' — ambiguous transport, cannot translate." >&2
      return 1
    fi
    if [ "$has_command" = "true" ]; then
      allowed_regex='^(command|args|env)$'
    else
      allowed_regex='^(url|headers)$'
    fi
    unknown="$(echo "$server" | jq -r --arg re "$allowed_regex" 'keys[] | select(test($re) | not)')"
    if [ -n "$unknown" ]; then
      echo "MCP server '$name' has unrecognized field(s): $(echo "$unknown" | tr '\n' ' ')." >&2
      return 1
    fi
  done <<< "$names"

  jq -n --argjson servers "$mcp_servers_json" '{mcpServers: $servers}'
}
```

- [ ] **Step 8: Run test to verify it passes**

Run: `bash bootstrap/mcp-translate-antigravity.test.sh`
Expected: `PASS: mcp_json_to_antigravity_config handles stdio, http, empty, and malformed input`

- [ ] **Step 9: Commit**

```bash
git add bootstrap/sync.ps1 bootstrap/sync.sh bootstrap/mcp-translate-antigravity.test.ps1 bootstrap/mcp-translate-antigravity.test.sh
git commit -m "Add MCP-to-Antigravity-config translator"
```

---

### Task 6: Codex `config.toml` merge mechanism

**Files:**
- Modify: `bootstrap/sync.ps1`
- Modify: `bootstrap/sync.sh`
- Create: `bootstrap/codex-mcp-merge.test.ps1`
- Create: `bootstrap/codex-mcp-merge.test.sh`

**Interfaces:**
- Consumes: TOML fragments in the shape `ConvertTo-CodexMcpToml`/`mcp_json_to_codex_toml` (Task 4) produce.
- Produces: `Merge-CodexMcpConfig -ConfigPath <path> -TomlByPlugin <hashtable: plugin name -> TOML fragment>` (void — writes the file). Wraps the managed content in `# >>> agent-extensions managed mcp_servers (do not edit within this block) >>>` / `# <<< agent-extensions managed mcp_servers <<<` marker comments; on each call, strips any existing block between those exact marker lines and replaces it, leaving everything else in the file untouched. Idempotent: identical input produces byte-identical output on repeat calls.
- Bash mirror: `merge_codex_mcp_config <config_path> <plugin1> <toml1> [<plugin2> <toml2> ...]` (writes the file, same marker-block behavior).

- [ ] **Step 1: Write the failing test (PowerShell)**

```powershell
# bootstrap/codex-mcp-merge.test.ps1
#!/usr/bin/env pwsh
$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "sync.ps1") -Import

$scratch = Join-Path ([System.IO.Path]::GetTempPath()) "agent-extensions-codexmerge-$(Get-Random)"
New-Item -ItemType Directory -Path $scratch -Force | Out-Null
$configPath = Join-Path $scratch "config.toml"

$unrelated = "[some_other_section]`nfoo = `"bar`"`n"
Set-Content -Path $configPath -Value $unrelated -NoNewline

$failures = @()

Merge-CodexMcpConfig -ConfigPath $configPath -TomlByPlugin @{
    "beta-mcp-stdio" = "[mcp_servers.fixture-stdio]`ncommand = `"node`""
    "gamma-mcp-http" = "[mcp_servers.fixture-http]`nurl = `"https://fixture.example.com/mcp`""
}
$afterFirst = Get-Content $configPath -Raw

if ($afterFirst -notmatch [regex]::Escape('[some_other_section]')) {
    $failures += "Merge dropped pre-existing unrelated content"
}
if ($afterFirst -notmatch [regex]::Escape('[mcp_servers.fixture-stdio]')) {
    $failures += "Merge did not add the stdio server table"
}
if ($afterFirst -notmatch [regex]::Escape('[mcp_servers.fixture-http]')) {
    $failures += "Merge did not add the http server table"
}

# --- Idempotency: identical re-merge produces identical output ---
Merge-CodexMcpConfig -ConfigPath $configPath -TomlByPlugin @{
    "beta-mcp-stdio" = "[mcp_servers.fixture-stdio]`ncommand = `"node`""
    "gamma-mcp-http" = "[mcp_servers.fixture-http]`nurl = `"https://fixture.example.com/mcp`""
}
$afterSecond = Get-Content $configPath -Raw
if ($afterSecond -ne $afterFirst) {
    $failures += "Re-merging identical input changed the file (not idempotent)"
}

# --- Changed input: old managed content replaced, unrelated content preserved ---
Merge-CodexMcpConfig -ConfigPath $configPath -TomlByPlugin @{
    "beta-mcp-stdio" = "[mcp_servers.fixture-stdio]`ncommand = `"node`""
}
$afterChange = Get-Content $configPath -Raw
if ($afterChange -match [regex]::Escape('[mcp_servers.fixture-http]')) {
    $failures += "Old managed content (fixture-http) was not removed after a changed merge"
}
if ($afterChange -notmatch [regex]::Escape('[some_other_section]')) {
    $failures += "Unrelated content was lost after a changed merge"
}

Remove-Item -Recurse -Force $scratch

if ($failures.Count -gt 0) {
    Write-Output "FAIL: Merge-CodexMcpConfig"
    $failures | ForEach-Object { Write-Output "  - $_" }
    exit 1
}

Write-Output "PASS: Merge-CodexMcpConfig preserves unrelated content, idempotent, replaces on change"
exit 0
```

- [ ] **Step 2: Run it to verify it fails**

Run: `pwsh bootstrap/codex-mcp-merge.test.ps1`
Expected: FAIL — `Merge-CodexMcpConfig` is not defined yet.

- [ ] **Step 3: Implement in `sync.ps1`**

Add this function, immediately after `ConvertTo-AntigravityMcpConfig`:

```powershell
function Merge-CodexMcpConfig {
    param([string]$ConfigPath, [hashtable]$TomlByPlugin)

    $beginMarker = "# >>> agent-extensions managed mcp_servers (do not edit within this block) >>>"
    $endMarker = "# <<< agent-extensions managed mcp_servers <<<"

    $existing = ""
    if (Test-Path $ConfigPath) {
        $existing = Get-Content $ConfigPath -Raw
    }

    $beginIdx = $existing.IndexOf($beginMarker)
    $endIdx = $existing.IndexOf($endMarker)
    if ($beginIdx -ge 0 -and $endIdx -ge 0) {
        $before = $existing.Substring(0, $beginIdx).TrimEnd()
        $after = $existing.Substring($endIdx + $endMarker.Length).TrimStart()
        $parts = @($before, $after) | Where-Object { $_ -ne "" }
        $existing = $parts -join "`n`n"
    } else {
        $existing = $existing.TrimEnd()
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pwsh bootstrap/codex-mcp-merge.test.ps1`
Expected: `PASS: Merge-CodexMcpConfig preserves unrelated content, idempotent, replaces on change`

- [ ] **Step 5: Write the failing test (Bash)**

```bash
# bootstrap/codex-mcp-merge.test.sh
#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=/dev/null
source "$REPO_ROOT/bootstrap/sync.sh" --import

SCRATCH="$(mktemp -d)"
CONFIG_PATH="$SCRATCH/config.toml"

printf '[some_other_section]\nfoo = "bar"\n' > "$CONFIG_PATH"

failures=()

merge_codex_mcp_config "$CONFIG_PATH" \
  "beta-mcp-stdio" '[mcp_servers.fixture-stdio]
command = "node"' \
  "gamma-mcp-http" '[mcp_servers.fixture-http]
url = "https://fixture.example.com/mcp"'

AFTER_FIRST="$(cat "$CONFIG_PATH")"
echo "$AFTER_FIRST" | grep -qF '[some_other_section]' || failures+=("Merge dropped pre-existing unrelated content")
echo "$AFTER_FIRST" | grep -qF '[mcp_servers.fixture-stdio]' || failures+=("Merge did not add the stdio server table")
echo "$AFTER_FIRST" | grep -qF '[mcp_servers.fixture-http]' || failures+=("Merge did not add the http server table")

merge_codex_mcp_config "$CONFIG_PATH" \
  "beta-mcp-stdio" '[mcp_servers.fixture-stdio]
command = "node"' \
  "gamma-mcp-http" '[mcp_servers.fixture-http]
url = "https://fixture.example.com/mcp"'
AFTER_SECOND="$(cat "$CONFIG_PATH")"
if [ "$AFTER_SECOND" != "$AFTER_FIRST" ]; then
  failures+=("Re-merging identical input changed the file (not idempotent)")
fi

merge_codex_mcp_config "$CONFIG_PATH" \
  "beta-mcp-stdio" '[mcp_servers.fixture-stdio]
command = "node"'
AFTER_CHANGE="$(cat "$CONFIG_PATH")"
if echo "$AFTER_CHANGE" | grep -qF '[mcp_servers.fixture-http]'; then
  failures+=("Old managed content (fixture-http) was not removed after a changed merge")
fi
echo "$AFTER_CHANGE" | grep -qF '[some_other_section]' || failures+=("Unrelated content was lost after a changed merge")

rm -rf "$SCRATCH"

if [ ${#failures[@]} -gt 0 ]; then
  echo "FAIL: merge_codex_mcp_config"
  printf '  - %s\n' "${failures[@]}"
  exit 1
fi

echo "PASS: merge_codex_mcp_config preserves unrelated content, idempotent, replaces on change"
exit 0
```

- [ ] **Step 6: Run it to verify it fails**

Run: `bash bootstrap/codex-mcp-merge.test.sh`
Expected: FAIL — `merge_codex_mcp_config` is not defined yet.

- [ ] **Step 7: Implement in `sync.sh`**

Add this function, immediately after `mcp_json_to_antigravity_config`:

```bash
merge_codex_mcp_config() {
  local config_path="$1"
  shift
  local begin_marker="# >>> agent-extensions managed mcp_servers (do not edit within this block) >>>"
  local end_marker="# <<< agent-extensions managed mcp_servers <<<"

  local existing=""
  [ -f "$config_path" ] && existing="$(cat "$config_path")"

  local before
  before="$(awk -v b="$begin_marker" -v e="$end_marker" '
    $0==b {skip=1; next}
    $0==e {skip=0; next}
    skip {next}
    {print}
  ' <<< "$existing")"

  local block="$begin_marker"$'\n'
  while [ $# -ge 2 ]; do
    local plugin="$1" toml="$2"
    shift 2
    [ -n "$toml" ] || continue
    block+="# plugin: $plugin"$'\n'
    block+="$toml"$'\n'
  done
  block+="$end_marker"

  mkdir -p "$(dirname "$config_path")"
  {
    if [ -n "$(printf '%s' "$before" | tr -d '[:space:]')" ]; then
      printf '%s\n\n' "$before"
    fi
    printf '%s\n' "$block"
  } > "$config_path"
}
```

- [ ] **Step 8: Run test to verify it passes**

Run: `bash bootstrap/codex-mcp-merge.test.sh`
Expected: `PASS: merge_codex_mcp_config preserves unrelated content, idempotent, replaces on change`

- [ ] **Step 9: Commit**

```bash
git add bootstrap/sync.ps1 bootstrap/sync.sh bootstrap/codex-mcp-merge.test.ps1 bootstrap/codex-mcp-merge.test.sh
git commit -m "Add Codex config.toml managed-block merge mechanism"
```

---

### Task 7: Antigravity per-plugin `mcp_config.json` writer

**Files:**
- Modify: `bootstrap/sync.ps1`
- Modify: `bootstrap/sync.sh`
- Create: `bootstrap/antigravity-mcp-write.test.ps1`
- Create: `bootstrap/antigravity-mcp-write.test.sh`

**Interfaces:**
- Consumes: JSON in the shape `ConvertTo-AntigravityMcpConfig`/`mcp_json_to_antigravity_config` (Task 5) produce.
- Produces: `Write-AntigravityMcpConfig -PluginStagedDir <path> -JsonContent <string>` (void — writes `<PluginStagedDir>/mcp_config.json`; a no-op if `JsonContent` is empty/whitespace). Unlike Task 6's merge, this file is entirely generated content with no user-authored parts to preserve, so each call fully overwrites it — idempotent by construction (same input always produces the same output).
- Bash mirror: `write_antigravity_mcp_config <plugin_staged_dir> <json_content>`.

- [ ] **Step 1: Write the failing test (PowerShell)**

```powershell
# bootstrap/antigravity-mcp-write.test.ps1
#!/usr/bin/env pwsh
$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "sync.ps1") -Import

$scratch = Join-Path ([System.IO.Path]::GetTempPath()) "agent-extensions-agmcpwrite-$(Get-Random)"
$pluginDir = Join-Path $scratch "fixture-http"

$failures = @()

$json = '{"mcpServers":{"fixture-http":{"url":"https://fixture.example.com/mcp"}}}'
Write-AntigravityMcpConfig -PluginStagedDir $pluginDir -JsonContent $json
$written = Get-Content (Join-Path $pluginDir "mcp_config.json") -Raw
if (($written | ConvertFrom-Json).mcpServers.'fixture-http'.url -ne "https://fixture.example.com/mcp") {
    $failures += "Written mcp_config.json does not round-trip the input JSON"
}

# --- Idempotent overwrite ---
Write-AntigravityMcpConfig -PluginStagedDir $pluginDir -JsonContent $json
$writtenAgain = Get-Content (Join-Path $pluginDir "mcp_config.json") -Raw
if ($writtenAgain -ne $written) {
    $failures += "Re-writing identical input changed the file (not idempotent)"
}

# --- Empty content is a no-op, not an empty file ---
$emptyDir = Join-Path $scratch "no-mcp-plugin"
Write-AntigravityMcpConfig -PluginStagedDir $emptyDir -JsonContent ""
if (Test-Path (Join-Path $emptyDir "mcp_config.json")) {
    $failures += "Empty JsonContent should not create mcp_config.json"
}

Remove-Item -Recurse -Force $scratch

if ($failures.Count -gt 0) {
    Write-Output "FAIL: Write-AntigravityMcpConfig"
    $failures | ForEach-Object { Write-Output "  - $_" }
    exit 1
}

Write-Output "PASS: Write-AntigravityMcpConfig writes, is idempotent, no-ops on empty input"
exit 0
```

- [ ] **Step 2: Run it to verify it fails**

Run: `pwsh bootstrap/antigravity-mcp-write.test.ps1`
Expected: FAIL — `Write-AntigravityMcpConfig` is not defined yet.

- [ ] **Step 3: Implement in `sync.ps1`**

Add this function, immediately after `Merge-CodexMcpConfig`:

```powershell
function Write-AntigravityMcpConfig {
    param([string]$PluginStagedDir, [string]$JsonContent)
    if ([string]::IsNullOrWhiteSpace($JsonContent)) { return }
    if (-not (Test-Path $PluginStagedDir)) {
        New-Item -ItemType Directory -Path $PluginStagedDir -Force | Out-Null
    }
    $target = Join-Path $PluginStagedDir "mcp_config.json"
    Set-Content -Path $target -Value $JsonContent -NoNewline
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pwsh bootstrap/antigravity-mcp-write.test.ps1`
Expected: `PASS: Write-AntigravityMcpConfig writes, is idempotent, no-ops on empty input`

- [ ] **Step 5: Write the failing test (Bash)**

```bash
# bootstrap/antigravity-mcp-write.test.sh
#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=/dev/null
source "$REPO_ROOT/bootstrap/sync.sh" --import

SCRATCH="$(mktemp -d)"
PLUGIN_DIR="$SCRATCH/fixture-http"

failures=()

JSON='{"mcpServers":{"fixture-http":{"url":"https://fixture.example.com/mcp"}}}'
write_antigravity_mcp_config "$PLUGIN_DIR" "$JSON"
WRITTEN="$(cat "$PLUGIN_DIR/mcp_config.json")"
[ "$(echo "$WRITTEN" | jq -r '.mcpServers."fixture-http".url')" = "https://fixture.example.com/mcp" ] \
  || failures+=("Written mcp_config.json does not round-trip the input JSON")

write_antigravity_mcp_config "$PLUGIN_DIR" "$JSON"
WRITTEN_AGAIN="$(cat "$PLUGIN_DIR/mcp_config.json")"
if [ "$WRITTEN_AGAIN" != "$WRITTEN" ]; then
  failures+=("Re-writing identical input changed the file (not idempotent)")
fi

EMPTY_DIR="$SCRATCH/no-mcp-plugin"
write_antigravity_mcp_config "$EMPTY_DIR" ""
if [ -f "$EMPTY_DIR/mcp_config.json" ]; then
  failures+=("Empty json_content should not create mcp_config.json")
fi

rm -rf "$SCRATCH"

if [ ${#failures[@]} -gt 0 ]; then
  echo "FAIL: write_antigravity_mcp_config"
  printf '  - %s\n' "${failures[@]}"
  exit 1
fi

echo "PASS: write_antigravity_mcp_config writes, is idempotent, no-ops on empty input"
exit 0
```

- [ ] **Step 6: Run it to verify it fails**

Run: `bash bootstrap/antigravity-mcp-write.test.sh`
Expected: FAIL — `write_antigravity_mcp_config` is not defined yet.

- [ ] **Step 7: Implement in `sync.sh`**

Add this function, immediately after `merge_codex_mcp_config`:

```bash
write_antigravity_mcp_config() {
  local plugin_staged_dir="$1"
  local json_content="$2"
  [ -n "$json_content" ] || return 0
  mkdir -p "$plugin_staged_dir"
  printf '%s' "$json_content" > "$plugin_staged_dir/mcp_config.json"
}
```

- [ ] **Step 8: Run test to verify it passes**

Run: `bash bootstrap/antigravity-mcp-write.test.sh`
Expected: `PASS: write_antigravity_mcp_config writes, is idempotent, no-ops on empty input`

- [ ] **Step 9: Commit**

```bash
git add bootstrap/sync.ps1 bootstrap/sync.sh bootstrap/antigravity-mcp-write.test.ps1 bootstrap/antigravity-mcp-write.test.sh
git commit -m "Add Antigravity per-plugin mcp_config.json writer"
```

---

### Task 8: Extend Codex sync to cover external plugins

**Files:**
- Modify: `bootstrap/sync.ps1`
- Modify: `bootstrap/sync.sh`
- Create: `bootstrap/sync-external-codex.test.ps1`
- Create: `bootstrap/sync-external-codex.test.sh`

**Interfaces:**
- Consumes: `Get-ExternalMarketplaces` (Task 3), `New-OrRepairJunction` (existing), `ConvertTo-CodexMcpToml` (Task 4), `Merge-CodexMcpConfig` (Task 6) — and their Bash equivalents.
- Produces: `Sync-ExternalCodexContent -RepoRoot <path> -VendorCacheDir <path> -CodexSkillsDir <path> -CodexConfigPath <path>` → array of failure-message strings (empty on full success). For every plugin declared under every marketplace: links every skill under `<VendorCacheDir>/<marketplace>/<plugin>/skills/*` into `CodexSkillsDir` (a plugin with no `skills/` directory contributes zero links, not a failure); if `<plugin>/.mcp.json` exists, translates it and merges the result into `CodexConfigPath`. One plugin's failure (bad skill link, bad translation) is recorded and does not stop the others.
- Bash mirror: `sync_external_codex_content <repo_root> <vendor_cache_dir> <codex_skills_dir> <codex_config_path>` — prints each failure to stderr, returns 0 on full success, 1 if any plugin failed.

- [ ] **Step 1: Write the failing test (PowerShell)**

```powershell
# bootstrap/sync-external-codex.test.ps1
#!/usr/bin/env pwsh
$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $PSScriptRoot
. (Join-Path $PSScriptRoot "sync.ps1") -Import
. (Join-Path $RepoRoot "bootstrap\tests\fixtures\marketplace-fixture.ps1")

$scratch = Join-Path ([System.IO.Path]::GetTempPath()) "agent-extensions-extcodex-$(Get-Random)"
$fixtureRepo = Join-Path $scratch "fixture-marketplace"
$declareRoot = Join-Path $scratch "declare-root"
$vendorCache = Join-Path $scratch "vendor-cache"
$codexSkillsDir = Join-Path $scratch "agents-skills"
$codexConfigPath = Join-Path $scratch "codex-config.toml"
New-Item -ItemType Directory -Path (Join-Path $declareRoot "bootstrap") -Force | Out-Null
New-Item -ItemType Directory -Path $codexSkillsDir -Force | Out-Null

$sha = New-FixtureMarketplace -DestDir $fixtureRepo
$manifest = @{
    marketplaces = @(
        @{ name = "fixture-mp"; repo = $fixtureRepo; pinnedCommit = $sha;
           plugins = @("alpha-skills", "beta-mcp-stdio", "gamma-mcp-http", "delta-malformed") }
    )
} | ConvertTo-Json -Depth 10
Set-Content -Path (Join-Path $declareRoot "bootstrap\external-marketplaces.json") -Value $manifest
Sync-VendorCache -RepoRoot $declareRoot -VendorCacheDir $vendorCache | Out-Null

$failures = @()

$reported = Sync-ExternalCodexContent -RepoRoot $declareRoot -VendorCacheDir $vendorCache -CodexSkillsDir $codexSkillsDir -CodexConfigPath $codexConfigPath

# --- Partial-failure isolation: delta-malformed fails, others still succeed ---
if ($reported.Count -eq 0) {
    $failures += "Expected a reported failure for delta-malformed, got none"
}

$greetLink = Join-Path $codexSkillsDir "greet"
$greetItem = Get-Item $greetLink -ErrorAction SilentlyContinue
if (-not $greetItem -or -not $greetItem.LinkType) {
    $failures += "alpha-skills: 'greet' skill was not linked (or is not a live link) despite delta-malformed's failure"
}

$configContent = if (Test-Path $codexConfigPath) { Get-Content $codexConfigPath -Raw } else { "" }
if ($configContent -notmatch [regex]::Escape('[mcp_servers.fixture-stdio]')) {
    $failures += "beta-mcp-stdio's server was not merged into config.toml"
}
if ($configContent -notmatch [regex]::Escape('[mcp_servers.fixture-http]')) {
    $failures += "gamma-mcp-http's server was not merged into config.toml"
}
if ($configContent -match 'fixture-bad') {
    $failures += "delta-malformed's unrecognized server should not appear in config.toml at all"
}

Remove-Item -Recurse -Force $scratch

if ($failures.Count -gt 0) {
    Write-Output "FAIL: Sync-ExternalCodexContent"
    $failures | ForEach-Object { Write-Output "  - $_" }
    exit 1
}

Write-Output "PASS: Sync-ExternalCodexContent links skills, merges MCP, isolates one plugin's failure"
exit 0
```

- [ ] **Step 2: Run it to verify it fails**

Run: `pwsh bootstrap/sync-external-codex.test.ps1`
Expected: FAIL — `Sync-ExternalCodexContent` is not defined yet.

- [ ] **Step 3: Implement in `sync.ps1`**

Add this function, immediately after `Sync-CodexSkills` (existing):

```powershell
function Sync-ExternalCodexContent {
    param([string]$RepoRoot, [string]$VendorCacheDir, [string]$CodexSkillsDir, [string]$CodexConfigPath)

    $marketplaces = Get-ExternalMarketplaces -RepoRoot $RepoRoot
    $failures = @()
    $tomlByPlugin = @{}

    foreach ($mp in $marketplaces) {
        foreach ($pluginName in $mp.plugins) {
            $pluginDir = Join-Path $VendorCacheDir "$($mp.name)\$pluginName"

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
                    $servers = if ($mcpJson.PSObject.Properties['mcpServers']) { $mcpJson.mcpServers } else { [PSCustomObject]@{} }
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pwsh bootstrap/sync-external-codex.test.ps1`
Expected: `PASS: Sync-ExternalCodexContent links skills, merges MCP, isolates one plugin's failure`

- [ ] **Step 5: Write the failing test (Bash)**

```bash
# bootstrap/sync-external-codex.test.sh
#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=/dev/null
source "$REPO_ROOT/bootstrap/sync.sh" --import
# shellcheck source=/dev/null
source "$REPO_ROOT/bootstrap/tests/fixtures/marketplace-fixture.sh"

SCRATCH="$(mktemp -d)"
FIXTURE_REPO="$SCRATCH/fixture-marketplace"
DECLARE_ROOT="$SCRATCH/declare-root"
VENDOR_CACHE="$SCRATCH/vendor-cache"
CODEX_SKILLS_DIR="$SCRATCH/agents-skills"
CODEX_CONFIG_PATH="$SCRATCH/codex-config.toml"
mkdir -p "$DECLARE_ROOT/bootstrap" "$CODEX_SKILLS_DIR"

SHA="$(new_fixture_marketplace "$FIXTURE_REPO")"
cat > "$DECLARE_ROOT/bootstrap/external-marketplaces.json" <<EOF
{
  "marketplaces": [
    { "name": "fixture-mp", "repo": "$FIXTURE_REPO", "pinnedCommit": "$SHA",
      "plugins": ["alpha-skills", "beta-mcp-stdio", "gamma-mcp-http", "delta-malformed"] }
  ]
}
EOF
sync_vendor_cache "$DECLARE_ROOT" "$VENDOR_CACHE"

failures=()

STDERR_CAPTURE="$SCRATCH/stderr-capture.txt"
REPORTED_OK=1
sync_external_codex_content "$DECLARE_ROOT" "$VENDOR_CACHE" "$CODEX_SKILLS_DIR" "$CODEX_CONFIG_PATH" 2>"$STDERR_CAPTURE" || REPORTED_OK=0

if [ "$REPORTED_OK" = "1" ]; then
  failures+=("Expected sync_external_codex_content to return non-zero for delta-malformed, it returned 0")
fi
if ! grep -q "delta-malformed" "$STDERR_CAPTURE"; then
  failures+=("Expected a reported failure mentioning delta-malformed on stderr")
fi

if [ ! -L "$CODEX_SKILLS_DIR/greet" ]; then
  failures+=("alpha-skills: 'greet' skill was not linked (or is not a live link) despite delta-malformed's failure")
fi

CONFIG_CONTENT=""
[ -f "$CODEX_CONFIG_PATH" ] && CONFIG_CONTENT="$(cat "$CODEX_CONFIG_PATH")"
echo "$CONFIG_CONTENT" | grep -qF '[mcp_servers.fixture-stdio]' || failures+=("beta-mcp-stdio's server was not merged into config.toml")
echo "$CONFIG_CONTENT" | grep -qF '[mcp_servers.fixture-http]' || failures+=("gamma-mcp-http's server was not merged into config.toml")
if echo "$CONFIG_CONTENT" | grep -q 'fixture-bad'; then
  failures+=("delta-malformed's unrecognized server should not appear in config.toml at all")
fi

rm -rf "$SCRATCH"

if [ ${#failures[@]} -gt 0 ]; then
  echo "FAIL: sync_external_codex_content"
  printf '  - %s\n' "${failures[@]}"
  exit 1
fi

echo "PASS: sync_external_codex_content links skills, merges MCP, isolates one plugin's failure"
exit 0
```

- [ ] **Step 6: Run it to verify it fails**

Run: `bash bootstrap/sync-external-codex.test.sh`
Expected: FAIL — `sync_external_codex_content` is not defined yet.

- [ ] **Step 7: Implement in `sync.sh`**

Add this function, immediately after `sync_codex_skills` (existing):

```bash
sync_external_codex_content() {
  local repo_root="$1"
  local vendor_cache_dir="$2"
  local codex_skills_dir="$3"
  local codex_config_path="$4"

  local failed=0
  local merge_args=()
  local mp_json name plugin_line plugin plugin_dir skills_root mcp_path mcp_servers toml

  while IFS= read -r mp_json; do
    [ -n "$mp_json" ] || continue
    name="$(echo "$mp_json" | jq -r '.name')"

    while IFS= read -r plugin_line; do
      plugin="$(echo "$plugin_line" | jq -r '.')"
      [ -n "$plugin" ] || continue
      plugin_dir="$vendor_cache_dir/$name/$plugin"

      skills_root="$plugin_dir/skills"
      if [ -d "$skills_root" ]; then
        local skill_dir skill_name
        for skill_dir in "$skills_root"/*/; do
          [ -d "$skill_dir" ] || continue
          skill_name="$(basename "$skill_dir")"
          if ! new_or_repair_symlink "$codex_skills_dir/$skill_name" "${skill_dir%/}"; then
            echo "Plugin '$plugin' (from '$name'): failed to link skill '$skill_name'" >&2
            failed=1
          fi
        done
      fi

      mcp_path="$plugin_dir/.mcp.json"
      if [ -f "$mcp_path" ]; then
        mcp_servers="$(jq -c '.mcpServers // {}' "$mcp_path")"
        if toml="$(mcp_json_to_codex_toml "$mcp_servers")"; then
          if [ -n "$toml" ]; then
            merge_args+=("$plugin" "$toml")
          fi
        else
          echo "Plugin '$plugin' (from '$name'): MCP translation to Codex TOML failed" >&2
          failed=1
        fi
      fi
    done < <(echo "$mp_json" | jq -c '.plugins[]')
  done < <(get_external_marketplaces_json "$repo_root")

  if [ ${#merge_args[@]} -gt 0 ]; then
    if ! merge_codex_mcp_config "$codex_config_path" "${merge_args[@]}"; then
      echo "Failed to merge translated MCP servers into '$codex_config_path'" >&2
      failed=1
    fi
  fi

  return $failed
}
```

- [ ] **Step 8: Run test to verify it passes**

Run: `bash bootstrap/sync-external-codex.test.sh`
Expected: `PASS: sync_external_codex_content links skills, merges MCP, isolates one plugin's failure`

- [ ] **Step 9: Commit**

```bash
git add bootstrap/sync.ps1 bootstrap/sync.sh bootstrap/sync-external-codex.test.ps1 bootstrap/sync-external-codex.test.sh
git commit -m "Extend Codex sync to cover external-marketplace plugins (skills + MCP)"
```

---

### Task 9: Extend Antigravity sync to cover external plugins

**Files:**
- Modify: `bootstrap/sync.ps1`
- Modify: `bootstrap/sync.sh`
- Create: `bootstrap/sync-external-antigravity.test.ps1`
- Create: `bootstrap/sync-external-antigravity.test.sh`

**Interfaces:**
- Consumes: `Get-ExternalMarketplaces` (Task 3), `New-OrRepairJunction` (existing), `ConvertTo-AntigravityMcpConfig` (Task 5), `Write-AntigravityMcpConfig` (Task 7) — and their Bash equivalents.
- Produces: `Sync-ExternalAntigravityContent -RepoRoot <path> -VendorCacheDir <path> -StagedDir <path> -AntigravityPluginsDir <path>` → array of failure-message strings. For every declared plugin: stages a directory at `<StagedDir>/antigravity/<plugin>/` containing a junction/symlink to the plugin's `skills/` (if present) plus a generated `mcp_config.json` (if the plugin ships MCP servers) — the pinned vendor-cache clone itself is never written into. Then junctions/symlinks `<AntigravityPluginsDir>/<plugin>` to that staged directory.
- Bash mirror: `sync_external_antigravity_content <repo_root> <vendor_cache_dir> <staged_dir> <antigravity_plugins_dir>`.

- [ ] **Step 1: Write the failing test (PowerShell)**

```powershell
# bootstrap/sync-external-antigravity.test.ps1
#!/usr/bin/env pwsh
$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $PSScriptRoot
. (Join-Path $PSScriptRoot "sync.ps1") -Import
. (Join-Path $RepoRoot "bootstrap\tests\fixtures\marketplace-fixture.ps1")

$scratch = Join-Path ([System.IO.Path]::GetTempPath()) "agent-extensions-extantigravity-$(Get-Random)"
$fixtureRepo = Join-Path $scratch "fixture-marketplace"
$declareRoot = Join-Path $scratch "declare-root"
$vendorCache = Join-Path $scratch "vendor-cache"
$stagedDir = Join-Path $scratch "staged"
$antigravityPluginsDir = Join-Path $scratch "gemini-plugins"
New-Item -ItemType Directory -Path (Join-Path $declareRoot "bootstrap") -Force | Out-Null
New-Item -ItemType Directory -Path $antigravityPluginsDir -Force | Out-Null

$sha = New-FixtureMarketplace -DestDir $fixtureRepo
$manifest = @{
    marketplaces = @(
        @{ name = "fixture-mp"; repo = $fixtureRepo; pinnedCommit = $sha;
           plugins = @("alpha-skills", "beta-mcp-stdio", "gamma-mcp-http", "delta-malformed") }
    )
} | ConvertTo-Json -Depth 10
Set-Content -Path (Join-Path $declareRoot "bootstrap\external-marketplaces.json") -Value $manifest
Sync-VendorCache -RepoRoot $declareRoot -VendorCacheDir $vendorCache | Out-Null

$failures = @()
$reported = Sync-ExternalAntigravityContent -RepoRoot $declareRoot -VendorCacheDir $vendorCache -StagedDir $stagedDir -AntigravityPluginsDir $antigravityPluginsDir

if ($reported.Count -eq 0) {
    $failures += "Expected a reported failure for delta-malformed, got none"
}

# --- alpha-skills: linked, contains its skill via the staged dir ---
$alphaLink = Join-Path $antigravityPluginsDir "alpha-skills"
$alphaItem = Get-Item $alphaLink -ErrorAction SilentlyContinue
if (-not $alphaItem -or -not $alphaItem.LinkType) {
    $failures += "alpha-skills was not linked into the Antigravity plugins dir"
}
if (-not (Test-Path (Join-Path $alphaLink "skills\greet\SKILL.md"))) {
    $failures += "alpha-skills' skill is not reachable through the Antigravity link"
}

# --- beta-mcp-stdio: linked, has generated mcp_config.json ---
$betaLink = Join-Path $antigravityPluginsDir "beta-mcp-stdio"
$betaConfig = Join-Path $betaLink "mcp_config.json"
if (-not (Test-Path $betaConfig)) {
    $failures += "beta-mcp-stdio's mcp_config.json was not generated/linked"
} else {
    $parsed = Get-Content $betaConfig -Raw | ConvertFrom-Json
    if ($parsed.mcpServers.'fixture-stdio'.command -ne "node") {
        $failures += "beta-mcp-stdio's mcp_config.json has wrong content"
    }
}

# --- delta-malformed: must not silently appear as if it succeeded ---
$deltaConfig = Join-Path $antigravityPluginsDir "delta-malformed\mcp_config.json"
if (Test-Path $deltaConfig) {
    $failures += "delta-malformed should not have produced an mcp_config.json"
}

# --- Never mutate the pinned vendor-cache clone ---
$vendorCloneMcpConfig = Join-Path $vendorCache "fixture-mp\beta-mcp-stdio\mcp_config.json"
if (Test-Path $vendorCloneMcpConfig) {
    $failures += "Sync wrote a generated file into the pinned vendor-cache clone — it must only write to the staged dir"
}

Remove-Item -Recurse -Force $scratch

if ($failures.Count -gt 0) {
    Write-Output "FAIL: Sync-ExternalAntigravityContent"
    $failures | ForEach-Object { Write-Output "  - $_" }
    exit 1
}

Write-Output "PASS: Sync-ExternalAntigravityContent stages+links plugins, generates MCP config, never mutates the clone"
exit 0
```

- [ ] **Step 2: Run it to verify it fails**

Run: `pwsh bootstrap/sync-external-antigravity.test.ps1`
Expected: FAIL — `Sync-ExternalAntigravityContent` is not defined yet.

- [ ] **Step 3: Implement in `sync.ps1`**

Add this function, immediately after `Sync-AntigravityPlugins` (existing):

```powershell
function Sync-ExternalAntigravityContent {
    param([string]$RepoRoot, [string]$VendorCacheDir, [string]$StagedDir, [string]$AntigravityPluginsDir)

    $marketplaces = Get-ExternalMarketplaces -RepoRoot $RepoRoot
    $failures = @()

    foreach ($mp in $marketplaces) {
        foreach ($pluginName in $mp.plugins) {
            $pluginDir = Join-Path $VendorCacheDir "$($mp.name)\$pluginName"
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
                    $servers = if ($mcpJson.PSObject.Properties['mcpServers']) { $mcpJson.mcpServers } else { [PSCustomObject]@{} }
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pwsh bootstrap/sync-external-antigravity.test.ps1`
Expected: `PASS: Sync-ExternalAntigravityContent stages+links plugins, generates MCP config, never mutates the clone`

- [ ] **Step 5: Write the failing test (Bash)**

```bash
# bootstrap/sync-external-antigravity.test.sh
#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=/dev/null
source "$REPO_ROOT/bootstrap/sync.sh" --import
# shellcheck source=/dev/null
source "$REPO_ROOT/bootstrap/tests/fixtures/marketplace-fixture.sh"

SCRATCH="$(mktemp -d)"
FIXTURE_REPO="$SCRATCH/fixture-marketplace"
DECLARE_ROOT="$SCRATCH/declare-root"
VENDOR_CACHE="$SCRATCH/vendor-cache"
STAGED_DIR="$SCRATCH/staged"
ANTIGRAVITY_DIR="$SCRATCH/gemini-plugins"
mkdir -p "$DECLARE_ROOT/bootstrap" "$ANTIGRAVITY_DIR"

SHA="$(new_fixture_marketplace "$FIXTURE_REPO")"
cat > "$DECLARE_ROOT/bootstrap/external-marketplaces.json" <<EOF
{
  "marketplaces": [
    { "name": "fixture-mp", "repo": "$FIXTURE_REPO", "pinnedCommit": "$SHA",
      "plugins": ["alpha-skills", "beta-mcp-stdio", "gamma-mcp-http", "delta-malformed"] }
  ]
}
EOF
sync_vendor_cache "$DECLARE_ROOT" "$VENDOR_CACHE"

failures=()

STDERR_CAPTURE="$SCRATCH/stderr-capture.txt"
REPORTED_OK=1
sync_external_antigravity_content "$DECLARE_ROOT" "$VENDOR_CACHE" "$STAGED_DIR" "$ANTIGRAVITY_DIR" 2>"$STDERR_CAPTURE" || REPORTED_OK=0
if [ "$REPORTED_OK" = "1" ]; then
  failures+=("Expected sync_external_antigravity_content to return non-zero for delta-malformed, it returned 0")
fi
if ! grep -q "delta-malformed" "$STDERR_CAPTURE"; then
  failures+=("Expected a reported failure mentioning delta-malformed on stderr")
fi

if [ ! -L "$ANTIGRAVITY_DIR/alpha-skills" ]; then
  failures+=("alpha-skills was not linked into the Antigravity plugins dir")
fi
if [ ! -f "$ANTIGRAVITY_DIR/alpha-skills/skills/greet/SKILL.md" ]; then
  failures+=("alpha-skills' skill is not reachable through the Antigravity link")
fi

BETA_CONFIG="$ANTIGRAVITY_DIR/beta-mcp-stdio/mcp_config.json"
if [ ! -f "$BETA_CONFIG" ]; then
  failures+=("beta-mcp-stdio's mcp_config.json was not generated/linked")
else
  [ "$(jq -r '.mcpServers."fixture-stdio".command' "$BETA_CONFIG")" = "node" ] \
    || failures+=("beta-mcp-stdio's mcp_config.json has wrong content")
fi

if [ -f "$ANTIGRAVITY_DIR/delta-malformed/mcp_config.json" ]; then
  failures+=("delta-malformed should not have produced an mcp_config.json")
fi

if [ -f "$VENDOR_CACHE/fixture-mp/beta-mcp-stdio/mcp_config.json" ]; then
  failures+=("Sync wrote a generated file into the pinned vendor-cache clone — it must only write to the staged dir")
fi

rm -rf "$SCRATCH"

if [ ${#failures[@]} -gt 0 ]; then
  echo "FAIL: sync_external_antigravity_content"
  printf '  - %s\n' "${failures[@]}"
  exit 1
fi

echo "PASS: sync_external_antigravity_content stages+links plugins, generates MCP config, never mutates the clone"
exit 0
```

- [ ] **Step 6: Run it to verify it fails**

Run: `bash bootstrap/sync-external-antigravity.test.sh`
Expected: FAIL — `sync_external_antigravity_content` is not defined yet.

- [ ] **Step 7: Implement in `sync.sh`**

Add this function, immediately after `sync_antigravity_plugins` (existing):

```bash
sync_external_antigravity_content() {
  local repo_root="$1"
  local vendor_cache_dir="$2"
  local staged_dir="$3"
  local antigravity_plugins_dir="$4"

  local failed=0
  local mp_json name plugin_line plugin plugin_dir staged_plugin_dir
  local skills_source mcp_path mcp_servers config final_link

  while IFS= read -r mp_json; do
    [ -n "$mp_json" ] || continue
    name="$(echo "$mp_json" | jq -r '.name')"

    while IFS= read -r plugin_line; do
      plugin="$(echo "$plugin_line" | jq -r '.')"
      [ -n "$plugin" ] || continue
      plugin_dir="$vendor_cache_dir/$name/$plugin"
      staged_plugin_dir="$staged_dir/antigravity/$plugin"
      mkdir -p "$staged_plugin_dir"

      skills_source="$plugin_dir/skills"
      if [ -d "$skills_source" ]; then
        if ! new_or_repair_symlink "$staged_plugin_dir/skills" "$skills_source"; then
          echo "Plugin '$plugin' (from '$name'): failed to link skills into staging" >&2
          failed=1
          continue
        fi
      fi

      mcp_path="$plugin_dir/.mcp.json"
      if [ -f "$mcp_path" ]; then
        mcp_servers="$(jq -c '.mcpServers // {}' "$mcp_path")"
        if config="$(mcp_json_to_antigravity_config "$mcp_servers")"; then
          write_antigravity_mcp_config "$staged_plugin_dir" "$config"
        else
          echo "Plugin '$plugin' (from '$name'): MCP translation to Antigravity config failed" >&2
          failed=1
          continue
        fi
      fi

      final_link="$antigravity_plugins_dir/$plugin"
      if ! new_or_repair_symlink "$final_link" "$staged_plugin_dir"; then
        echo "Plugin '$plugin' (from '$name'): failed to link into Antigravity plugins dir" >&2
        failed=1
      fi
    done < <(echo "$mp_json" | jq -c '.plugins[]')
  done < <(get_external_marketplaces_json "$repo_root")

  return $failed
}
```

- [ ] **Step 8: Run test to verify it passes**

Run: `bash bootstrap/sync-external-antigravity.test.sh`
Expected: `PASS: sync_external_antigravity_content stages+links plugins, generates MCP config, never mutates the clone`

- [ ] **Step 9: Commit**

```bash
git add bootstrap/sync.ps1 bootstrap/sync.sh bootstrap/sync-external-antigravity.test.ps1 bootstrap/sync-external-antigravity.test.sh
git commit -m "Extend Antigravity sync to cover external-marketplace plugins (skills + MCP)"
```

---

### Task 10: Extend Claude Code marketplace sync + live-CLI test

**Files:**
- Modify: `bootstrap/sync.ps1` (the existing `Sync-ClaudeCodeMarketplace` function)
- Modify: `bootstrap/sync.sh` (the existing `sync_claude_code_marketplace` function)
- Modify: `bootstrap/sync.claude-code.test.ps1` (existing live-CLI test, extend it)

**Interfaces:**
- Consumes: `Get-ExternalMarketplaces` (Task 3) / `get_external_marketplaces_json` (Task 3).
- Produces (changed): `Sync-ClaudeCodeMarketplace -RepoRoot <path>` now returns an array of failure-message strings instead of void. It still registers and installs this repo's own 2 plugins exactly as before (`claude plugin marketplace add`, `claude plugin install`) and still **throws immediately** if either of those fails — that part is unchanged, load-bearing behavior. New: for every declared external marketplace, it additionally runs `claude plugin marketplace add <repo>` and `claude plugin install <plugin>@<marketplace>` for every declared plugin; a failure in this new part is **collected into the returned array**, not thrown, so one bad external marketplace/plugin doesn't stop the others or abort registration of this repo's own plugins.
- Bash mirror: `sync_claude_code_marketplace <repo_root>` — same behavior split (own-plugin failures still `return 1` immediately via existing `if ! ...; then return 1; fi`; external-plugin failures are printed to stderr and recorded, function returns 1 at the end if any occurred, after attempting every declared external plugin).

This task is the one place this plan deliberately deviates from scratch-isolated testing, extending the existing precedent: `claude plugin marketplace add`/`claude plugin install` have no sandbox flag, so `sync.claude-code.test.ps1` already runs for real against Kyle's machine. There is no `sync.claude-code.test.sh` — that live-test precedent has only ever existed for PowerShell; this task keeps that boundary rather than introducing a new one.

- [ ] **Step 1: Extend the existing live test (PowerShell) — write the new assertions**

Add to `bootstrap/sync.claude-code.test.ps1`, after the existing idempotency check and before the final `if ($failures.Count -gt 0)` block:

```powershell
# --- External marketplace: register one real, small plugin end-to-end ---
$externalFailures = Sync-ClaudeCodeMarketplace -RepoRoot $RepoRoot
if ($externalFailures.Count -gt 0) {
    $failures += "External marketplace sync reported failures: $($externalFailures -join '; ')"
}

$listOutput3 = & claude plugin list 2>&1 | Out-String
if ($listOutput3 -notmatch "elements-of-style@superpowers-marketplace") {
    $failures += "claude plugin list does not show elements-of-style@superpowers-marketplace after external sync"
}

# --- Idempotency for the external part too ---
Sync-ClaudeCodeMarketplace -RepoRoot $RepoRoot | Out-Null
$listOutput4 = & claude plugin list 2>&1 | Out-String
if ($listOutput4 -notmatch "elements-of-style@superpowers-marketplace") {
    $failures += "Second sync run lost the elements-of-style@superpowers-marketplace registration"
}
```

- [ ] **Step 2: Run it to verify it fails**

Run: `pwsh bootstrap/sync.claude-code.test.ps1`
Expected: FAIL — `elements-of-style@superpowers-marketplace` is not declared in `external-marketplaces.json` in a way `Sync-ClaudeCodeMarketplace` currently reads (the function doesn't process external marketplaces at all yet, so no new registration happens; the assertion checking for it fails).

- [ ] **Step 3: Modify `Sync-ClaudeCodeMarketplace` in `sync.ps1`**

Replace the existing function body:

```powershell
function Sync-ClaudeCodeMarketplace {
    param([string]$RepoRoot)

    & claude plugin marketplace add $RepoRoot
    if ($LASTEXITCODE -ne 0) {
        throw "claude plugin marketplace add '$RepoRoot' failed with exit code $LASTEXITCODE"
    }

    foreach ($plugin in (Get-PluginNames -RepoRoot $RepoRoot)) {
        & claude plugin install "$plugin@agent-extensions"
        if ($LASTEXITCODE -ne 0) {
            throw "claude plugin install '$plugin@agent-extensions' failed with exit code $LASTEXITCODE"
        }
    }

    $failures = @()
    foreach ($mp in (Get-ExternalMarketplaces -RepoRoot $RepoRoot)) {
        & claude plugin marketplace add $mp.repo
        if ($LASTEXITCODE -ne 0) {
            $failures += "claude plugin marketplace add '$($mp.repo)' failed with exit code $LASTEXITCODE"
            continue
        }
        foreach ($pluginName in $mp.plugins) {
            & claude plugin install "$pluginName@$($mp.name)"
            if ($LASTEXITCODE -ne 0) {
                $failures += "claude plugin install '$pluginName@$($mp.name)' failed with exit code $LASTEXITCODE"
            }
        }
    }
    return $failures
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pwsh bootstrap/sync.claude-code.test.ps1`
Expected: `PASS: Claude Code marketplace + both plugins registered and idempotent` (the existing pass message — this task doesn't change it, it just adds more assertions before it; rename the final `Write-Output` line to `"PASS: Claude Code marketplace, own plugins, and one external marketplace/plugin registered and idempotent"` to reflect the expanded coverage).

- [ ] **Step 5: Modify `sync_claude_code_marketplace` in `sync.sh`**

Replace the existing function body:

```bash
sync_claude_code_marketplace() {
  local repo_root="$1"

  if ! claude plugin marketplace add "$repo_root"; then
    echo "claude plugin marketplace add '$repo_root' failed" >&2
    return 1
  fi

  local plugin_names
  mapfile -t plugin_names < <(get_plugin_names "$repo_root") || return 1
  if [ ${#plugin_names[@]} -eq 0 ]; then
    echo "No plugins found under '$repo_root/plugins'" >&2
    return 1
  fi

  local plugin
  for plugin in "${plugin_names[@]}"; do
    if ! claude plugin install "$plugin@agent-extensions"; then
      echo "claude plugin install '$plugin@agent-extensions' failed" >&2
      return 1
    fi
  done

  local failed=0
  local mp_json name repo plugin_line pname
  while IFS= read -r mp_json; do
    [ -n "$mp_json" ] || continue
    name="$(echo "$mp_json" | jq -r '.name')"
    repo="$(echo "$mp_json" | jq -r '.repo')"

    if ! claude plugin marketplace add "$repo"; then
      echo "claude plugin marketplace add '$repo' failed" >&2
      failed=1
      continue
    fi

    while IFS= read -r plugin_line; do
      pname="$(echo "$plugin_line" | jq -r '.')"
      [ -n "$pname" ] || continue
      if ! claude plugin install "$pname@$name"; then
        echo "claude plugin install '$pname@$name' failed" >&2
        failed=1
      fi
    done < <(echo "$mp_json" | jq -c '.plugins[]')
  done < <(get_external_marketplaces_json "$repo_root")

  return $failed
}
```

- [ ] **Step 6: Run the full test suite once more to confirm nothing else broke**

Run each in sequence: `pwsh bootstrap/sync.test.ps1`, `bash bootstrap/sync.test.sh`, `pwsh bootstrap/vendor-cache.test.ps1`, `bash bootstrap/vendor-cache.test.sh`, `pwsh bootstrap/sync.claude-code.test.ps1`
Expected: all `PASS`.

- [ ] **Step 7: Commit**

```bash
git add bootstrap/sync.ps1 bootstrap/sync.sh bootstrap/sync.claude-code.test.ps1
git commit -m "Extend Claude Code marketplace sync to register external marketplaces/plugins"
```

---

### Task 11: Wire the full sync flow together, update docs, verify live

**Files:**
- Modify: `bootstrap/sync.ps1` (top-level execution block)
- Modify: `bootstrap/sync.sh` (top-level execution block)
- Modify: `.gitignore`
- Modify: `AGENTS.md`
- Modify: `README.md`

**Interfaces:**
- Consumes: every function from Tasks 1–10.
- Produces: the final, runnable `sync.ps1`/`sync.sh` — the actual deliverable Kyle runs.

This task has no new isolated function to TDD — it wires already-tested pieces together, then proves the result by running the real thing. Verification is the design doc's 6 acceptance criteria, checked for real.

- [ ] **Step 1: Add `.vendor-cache/` to `.gitignore`**

```
.worktrees/
.vendor-cache/
.DS_Store
Thumbs.db
*.swp
```

Verify it's actually ignored: `git check-ignore -v .vendor-cache` (from repo root, after creating an empty `.vendor-cache/` dir) — expected: prints a match against the `.gitignore` line, confirming git will ignore it.

- [ ] **Step 2: Rewrite the top-level execution block in `sync.ps1`**

Replace the final block (from `if ($Import) { return }` to the end of the file):

```powershell
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
```

Note: `Sync-CodexSkills`/`Sync-AntigravityPlugins` gain `| Out-Null` here (they return `$true`, which would otherwise print stray `True` lines above the new structured failure report — a pre-existing cosmetic quirk this task fixes only because it's directly editing this exact block, not a separate unrelated change).

- [ ] **Step 3: Rewrite the top-level execution block in `sync.sh`**

Replace the final block (from `if [ "${1:-}" = "--import" ]; then` to the end of the file):

```bash
if [ "${1:-}" = "--import" ]; then
  return 0 2>/dev/null || exit 0
fi

VENDOR_CACHE_DIR="$REPO_ROOT/.vendor-cache"
STAGED_DIR="$VENDOR_CACHE_DIR/_staged"
CODEX_CONFIG_PATH="$HOME/.codex/config.toml"

overall_failed=0

sync_codex_skills "$REPO_ROOT" "$CODEX_SKILLS_DIR" || overall_failed=1
sync_antigravity_plugins "$REPO_ROOT" "$ANTIGRAVITY_PLUGINS_DIR" || overall_failed=1

sync_vendor_cache "$REPO_ROOT" "$VENDOR_CACHE_DIR" || overall_failed=1
sync_external_codex_content "$REPO_ROOT" "$VENDOR_CACHE_DIR" "$CODEX_SKILLS_DIR" "$CODEX_CONFIG_PATH" || overall_failed=1
sync_external_antigravity_content "$REPO_ROOT" "$VENDOR_CACHE_DIR" "$STAGED_DIR" "$ANTIGRAVITY_PLUGINS_DIR" || overall_failed=1

if [ -z "$SKIP_CLAUDE_CODE" ]; then
  sync_claude_code_marketplace "$REPO_ROOT" || overall_failed=1
fi

if [ "$overall_failed" -ne 0 ]; then
  echo "Sync completed with failures (see messages above)." >&2
  exit 1
fi

echo "Sync complete."
```

- [ ] **Step 4: Update `AGENTS.md`**

Add a new section after "Adding a new skill":

```markdown
## External marketplaces

`bootstrap/external-marketplaces.json` declares Claude Code marketplaces this
repo depends on (currently `claude-plugins-official`, `superpowers-marketplace`)
and which of their plugins to sync. `sync.ps1`/`sync.sh` clone each to a pinned
commit under `.vendor-cache/` (gitignored, not committed), then port Skills and
MCP servers from those plugins to Codex and Antigravity the same way this
repo's own plugins are — Claude Code registers the marketplaces natively and
tracks their live HEAD, same as it already does for any marketplace you add
by hand.

To add a plugin: add its name to the right marketplace's `plugins` array in
`external-marketplaces.json`, then re-run sync. To bump a marketplace's pinned
commit: update `pinnedCommit`, then re-run sync — this is a manual, deliberate
action, same as re-syncing a vendored skill.

Agents, hooks, and commands from these plugins are not ported — see
`docs/superpowers/specs/2026-08-20-full-plugin-support-design.md` for why.
```

- [ ] **Step 5: Update `README.md`**

Add one sentence after the existing "Install" section's code block:

```markdown
This also syncs Skills and MCP servers from the external marketplaces
declared in `bootstrap/external-marketplaces.json` — see
[AGENTS.md](./AGENTS.md#external-marketplaces).
```

- [ ] **Step 6: Run the full test suite**

Run in sequence:
```
pwsh bootstrap/tests/fixtures/marketplace-fixture.test.ps1
bash bootstrap/tests/fixtures/marketplace-fixture.test.sh
pwsh bootstrap/vendor-cache.test.ps1
bash bootstrap/vendor-cache.test.sh
pwsh bootstrap/mcp-translate.test.ps1
bash bootstrap/mcp-translate.test.sh
pwsh bootstrap/mcp-translate-antigravity.test.ps1
bash bootstrap/mcp-translate-antigravity.test.sh
pwsh bootstrap/codex-mcp-merge.test.ps1
bash bootstrap/codex-mcp-merge.test.sh
pwsh bootstrap/antigravity-mcp-write.test.ps1
bash bootstrap/antigravity-mcp-write.test.sh
pwsh bootstrap/sync-external-codex.test.ps1
bash bootstrap/sync-external-codex.test.sh
pwsh bootstrap/sync-external-antigravity.test.ps1
bash bootstrap/sync-external-antigravity.test.sh
pwsh bootstrap/sync.test.ps1
bash bootstrap/sync.test.sh
pwsh bootstrap/sync.claude-code.test.ps1
```
Expected: every one prints a `PASS:` line and exits 0.

- [ ] **Step 7: Commit the wiring and docs**

```bash
git add bootstrap/sync.ps1 bootstrap/sync.sh .gitignore AGENTS.md README.md
git commit -m "Wire external-marketplace sync into the main flow; document it"
```

- [ ] **Step 8: Run the real sync end-to-end and verify each acceptance criterion**

Run: `pwsh bootstrap/sync.ps1`
Expected: exits 0, prints `Sync complete.` (if it instead reports failures, read every one — a failure here on real content, e.g. an MCP field shape this plan's translators didn't anticipate, is expected to be possible on the first real run against all 39 plugins; fix the specific unrecognized-field case in `ConvertTo-CodexMcpToml`/`ConvertTo-AntigravityMcpConfig` and their Bash mirrors, re-run, and note in the commit message which real plugin's MCP shape extended the recognized-field set — this is the risk the design doc's Risks section explicitly anticipated, not a plan defect).

Then verify, per the design doc's acceptance criteria:

1. **Claude Code**: `claude plugin list` includes all 39 external plugins as `<plugin>@<marketplace>`, enabled.
2. **Codex**: spot-check a sample — `~/.agents/skills/` contains links for skills from at least one external plugin that ships them (e.g. from `superpowers`, if it ships a `skills/` directory — check with `Get-ChildItem`/`ls` on the vendor-cache clone first to confirm which external plugins actually have one); `~/.codex/config.toml` contains `[mcp_servers.*]` entries for MCP-bearing plugins like `postman`, `figma`, `supabase`, `auth0`, `mintlify`, `sourcegraph`, `private-journal-mcp`.
3. **Antigravity**: `~/.gemini/config/plugins/` contains a link per external plugin; spot-check that at least one MCP-bearing plugin's `mcp_config.json` is present and non-empty. **Explicitly verify, don't assume, whether Antigravity's own plugin loader actually recognizes these folders** (check Antigravity's own plugin-list command/UI if available) — this was flagged as an unverified risk in the design doc; report the real answer here rather than treating "the junction exists" as proof it works.
4. **Idempotency**: run `pwsh bootstrap/sync.ps1` a second time — exits 0, `Sync complete.`, no duplicate registrations.
5. **Content integrity**: pick 2–3 MCP-bearing plugins and manually diff their `~/.codex/config.toml` entries against their source `.mcp.json` — command/args/env or url/headers must match exactly.
6. **Partial-failure isolation**: already covered by Task 8/9's automated tests against the `delta-malformed` fixture; no further live check needed.

- [ ] **Step 9: Record what Step 8 found**

If any real plugin's `.mcp.json` needed a translator fix, that fix is already committed as part of Step 8's iteration. If Antigravity's loader does **not** recognize the linked plugins, do not silently mark this task done — report it plainly as an open risk (matching the design doc's own honesty about this being unverified) rather than claiming full cross-provider parity.

```bash
git add -A
git commit -m "Real end-to-end sync verified against all 39 external plugins"
```

