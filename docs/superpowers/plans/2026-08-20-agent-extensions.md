# agent-extensions v1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stand up a private GitHub repo (`kgsmith19/agent-extensions`) distributing two Claude Skills plugins (`anthropic-product-skills`, `general-skills`) to Claude Code, Codex, and Antigravity via one idempotent sync script, with the 3 skills currently on Kyle's claude.ai account vendored from Anthropic's official source.

**Architecture:** Two plugin directories under `plugins/`, each with one canonical `skills/` folder and three thin per-harness manifests (Claude Code, Antigravity, Codex) pointing at it. A repo-root `marketplace.json` lists both plugins for Claude Code discovery. `bootstrap/sync.ps1` (Windows) and `bootstrap/sync.sh` (Unix/cloud) each perform three idempotent actions: register/install with Claude Code via the `claude` CLI, junction/symlink each skill into Codex's `~/.agents/skills/`, and junction/symlink each plugin into Antigravity's `~/.gemini/config/plugins/`.

**Tech Stack:** PowerShell 7+ (Windows sync + tests), POSIX shell (Unix sync), JSON (plugin/marketplace manifests), Markdown+YAML frontmatter (skills, unchanged from source). No new language runtime, no test framework dependency — plain scripts with explicit PASS/FAIL assertions and exit codes, matching this ecosystem's existing convention (see `hyperbolic-core/apps/agentic-command-center/backend/shim/claude.test.ps1`).

## Global Constraints

- No absolute local path (`C:\Users\...`, `C:\code\...`) is ever committed to this repo. Every machine-specific path is derived at sync time from `$env:USERPROFILE` (Windows) / `$HOME` (Unix).
- The repo is private on GitHub (`kgsmith19/agent-extensions`), pushed at the end of this plan, not before — nothing is pushed until content and manifests are verified locally.
- Every vendored skill's `SKILL.md` frontmatter `name` field must equal its containing folder name — this is asserted, not assumed.
- `sync.ps1` / `sync.sh` must be idempotent: running twice produces no errors, no duplicate registrations, no broken links.
- A junction/symlink creation failure is always loud (non-zero exit, explicit message) — never a silent no-op.
- Two exact plugin name strings are used everywhere and must never drift: `anthropic-product-skills` and `general-skills`.

---

## Task 1: Vendor the 3 skills from anthropics/skills

**Files:**
- Create: `plugins/anthropic-product-skills/skills/canvas-design/` (full contents from source)
- Create: `plugins/anthropic-product-skills/skills/web-artifacts-builder/` (full contents from source)
- Create: `plugins/general-skills/skills/skill-creator/` (full contents from source)
- Create: `plugins/anthropic-product-skills/skills/VENDORED-FROM`
- Create: `plugins/general-skills/skills/VENDORED-FROM`
- Create: `LICENSE` (Apache 2.0, copied from `anthropics/skills`)
- Test: `bootstrap/verify-content.ps1`

**Interfaces:**
- Consumes: nothing (first task)
- Produces: skill folder paths above, consumed by Task 2 (manifests reference them) and Task 4 (sync links them). `VENDORED-FROM` line format: `<skill-name> <commit-sha> <date>`, one line per skill, consumed by nothing yet in v1 (documented for a future re-sync task).

- [ ] **Step 1: Write the failing verification script**

Create `bootstrap/verify-content.ps1`:

```powershell
#!/usr/bin/env pwsh
# Verifies every vendored skill has a SKILL.md whose frontmatter `name`
# matches its folder name, and that LICENSE + VENDORED-FROM exist.
param(
    [string]$RepoRoot = (Split-Path -Parent $PSScriptRoot)
)

$ErrorActionPreference = "Stop"
$failures = @()

$expected = @{
    "anthropic-product-skills" = @("canvas-design", "web-artifacts-builder")
    "general-skills"           = @("skill-creator")
}

if (-not (Test-Path (Join-Path $RepoRoot "LICENSE"))) {
    $failures += "Missing $RepoRoot\LICENSE"
}

foreach ($plugin in $expected.Keys) {
    $skillsDir = Join-Path $RepoRoot "plugins\$plugin\skills"
    $vendoredFrom = Join-Path $skillsDir "VENDORED-FROM"

    if (-not (Test-Path $vendoredFrom)) {
        $failures += "Missing $vendoredFrom"
    }

    foreach ($skill in $expected[$plugin]) {
        $skillMd = Join-Path $skillsDir "$skill\SKILL.md"
        if (-not (Test-Path $skillMd)) {
            $failures += "Missing $skillMd"
            continue
        }
        $content = Get-Content $skillMd -Raw
        if ($content -notmatch '(?ms)^---\s*.*?^name:\s*([^\r\n]+)') {
            $failures += "$skillMd has no frontmatter 'name' field"
            continue
        }
        $foundName = $Matches[1].Trim()
        if ($foundName -ne $skill) {
            $failures += "$skillMd frontmatter name '$foundName' does not match folder name '$skill'"
        }
    }
}

if ($failures.Count -gt 0) {
    Write-Output "FAIL: content verification"
    $failures | ForEach-Object { Write-Output "  - $_" }
    exit 1
}

Write-Output "PASS: all vendored skills present, named correctly, LICENSE and VENDORED-FROM present"
exit 0
```

- [ ] **Step 2: Run it to verify it fails**

Run: `pwsh bootstrap/verify-content.ps1`
Expected: `FAIL: content verification` listing every missing `LICENSE`, `VENDORED-FROM`, and `SKILL.md` path (nothing vendored yet).

- [ ] **Step 3: Vendor the content**

```bash
TMPDIR=$(mktemp -d)
git clone --depth 1 https://github.com/anthropics/skills "$TMPDIR/skills"
SHA=$(git -C "$TMPDIR/skills" rev-parse HEAD)
DATE=$(date -u +%Y-%m-%d)

mkdir -p "plugins/anthropic-product-skills/skills"
mkdir -p "plugins/general-skills/skills"

cp -r "$TMPDIR/skills/skills/canvas-design" "plugins/anthropic-product-skills/skills/canvas-design"
cp -r "$TMPDIR/skills/skills/web-artifacts-builder" "plugins/anthropic-product-skills/skills/web-artifacts-builder"
cp -r "$TMPDIR/skills/skills/skill-creator" "plugins/general-skills/skills/skill-creator"

cp "$TMPDIR/skills/LICENSE" "LICENSE"

printf "canvas-design %s %s\nweb-artifacts-builder %s %s\n" "$SHA" "$DATE" "$SHA" "$DATE" \
  > "plugins/anthropic-product-skills/skills/VENDORED-FROM"
printf "skill-creator %s %s\n" "$SHA" "$DATE" \
  > "plugins/general-skills/skills/VENDORED-FROM"

rm -rf "$TMPDIR"
```

- [ ] **Step 4: Run the verification script to confirm it passes**

Run: `pwsh bootstrap/verify-content.ps1`
Expected: `PASS: all vendored skills present, named correctly, LICENSE and VENDORED-FROM present`, exit code 0.

- [ ] **Step 5: Commit**

```bash
git add plugins/anthropic-product-skills/skills plugins/general-skills/skills LICENSE bootstrap/verify-content.ps1
git commit -m "Vendor canvas-design, web-artifacts-builder, skill-creator from anthropics/skills"
```

---

## Task 2: Plugin and marketplace manifests

**Files:**
- Create: `plugins/anthropic-product-skills/.claude-plugin/plugin.json`
- Create: `plugins/anthropic-product-skills/plugin.json` (Antigravity)
- Create: `plugins/anthropic-product-skills/.codex-plugin/plugin.json`
- Create: `plugins/general-skills/.claude-plugin/plugin.json`
- Create: `plugins/general-skills/plugin.json` (Antigravity)
- Create: `plugins/general-skills/.codex-plugin/plugin.json`
- Create: `.claude-plugin/marketplace.json`
- Test: manual invocation of `claude plugin validate` (the real consuming tool — no hand-rolled schema check duplicates what it already does)

**Interfaces:**
- Consumes: skill folder paths from Task 1 (`plugins/anthropic-product-skills/skills/`, `plugins/general-skills/skills/`)
- Produces: the two exact plugin name strings (`anthropic-product-skills`, `general-skills`) that Task 4 and Task 5's sync functions reference by name, and that `marketplace.json`'s `plugins[].source` paths point at (`./plugins/anthropic-product-skills`, `./plugins/general-skills`)

- [ ] **Step 1: Write the manifests**

`plugins/anthropic-product-skills/.claude-plugin/plugin.json`:
```json
{
  "name": "anthropic-product-skills",
  "description": "Claude.ai product-surface skills (Canvas, Artifacts), vendored from anthropics/skills",
  "version": "1.0.0",
  "author": { "name": "Kyle Smith" },
  "license": "Apache-2.0",
  "keywords": ["canvas", "artifacts", "claude-only"]
}
```

`plugins/anthropic-product-skills/plugin.json`:
```json
{
  "name": "anthropic-product-skills"
}
```

`plugins/anthropic-product-skills/.codex-plugin/plugin.json`:
```json
{
  "name": "anthropic-product-skills",
  "version": "1.0.0",
  "description": "Claude.ai product-surface skills (Canvas, Artifacts), vendored from anthropics/skills",
  "skills": "./skills/"
}
```

`plugins/general-skills/.claude-plugin/plugin.json`:
```json
{
  "name": "general-skills",
  "description": "Cross-provider personal skills, starting with skill-creator (vendored from anthropics/skills)",
  "version": "1.0.0",
  "author": { "name": "Kyle Smith" },
  "license": "Apache-2.0",
  "keywords": ["skill-authoring"]
}
```

`plugins/general-skills/plugin.json`:
```json
{
  "name": "general-skills"
}
```

`plugins/general-skills/.codex-plugin/plugin.json`:
```json
{
  "name": "general-skills",
  "version": "1.0.0",
  "description": "Cross-provider personal skills, starting with skill-creator (vendored from anthropics/skills)",
  "skills": "./skills/"
}
```

`.claude-plugin/marketplace.json`:
```json
{
  "name": "agent-extensions",
  "description": "Kyle's personal cross-provider skills marketplace",
  "owner": { "name": "Kyle Smith" },
  "plugins": [
    {
      "name": "anthropic-product-skills",
      "description": "Claude.ai product-surface skills (Canvas, Artifacts), vendored from anthropics/skills",
      "version": "1.0.0",
      "source": "./plugins/anthropic-product-skills",
      "author": { "name": "Kyle Smith" }
    },
    {
      "name": "general-skills",
      "description": "Cross-provider personal skills, starting with skill-creator (vendored from anthropics/skills)",
      "version": "1.0.0",
      "source": "./plugins/general-skills",
      "author": { "name": "Kyle Smith" }
    }
  ]
}
```

- [ ] **Step 2: Run the failing check first**

Before writing the files above, run:
Run: `claude plugin validate plugins/anthropic-product-skills`
Expected: FAIL — `.claude-plugin/plugin.json` not found.

(This step documents the red state. If executing tasks strictly in written order, Step 1 already wrote the files — run this validation BEFORE Step 1's files exist, i.e. swap execution order to: attempt validation first noting the failure, then write files, then re-validate in Step 3.)

- [ ] **Step 3: Run validation to confirm it passes**

Run: `claude plugin validate plugins/anthropic-product-skills`
Expected: PASS, no errors.

Run: `claude plugin validate plugins/general-skills`
Expected: PASS, no errors.

Run: `claude plugin validate .claude-plugin/marketplace.json`
Expected: PASS, both plugins listed with no schema errors.

- [ ] **Step 4: Commit**

```bash
git add plugins/anthropic-product-skills/.claude-plugin plugins/anthropic-product-skills/plugin.json plugins/anthropic-product-skills/.codex-plugin \
        plugins/general-skills/.claude-plugin plugins/general-skills/plugin.json plugins/general-skills/.codex-plugin \
        .claude-plugin/marketplace.json
git commit -m "Add Claude Code, Antigravity, and Codex plugin manifests + marketplace.json"
```

---

## Task 3: Repo root pointer files

**Files:**
- Create: `AGENTS.md`
- Create: `CLAUDE.md`
- Create: `GEMINI.md`
- Create: `README.md`
- Test: `bootstrap/verify-pointers.ps1`

**Interfaces:**
- Consumes: nothing new
- Produces: nothing consumed by later tasks — documentation only

- [ ] **Step 1: Write the failing pointer-file check**

Create `bootstrap/verify-pointers.ps1`:

```powershell
#!/usr/bin/env pwsh
param(
    [string]$RepoRoot = (Split-Path -Parent $PSScriptRoot)
)

$ErrorActionPreference = "Stop"
$failures = @()

$agentsPath = Join-Path $RepoRoot "AGENTS.md"
if (-not (Test-Path $agentsPath)) {
    $failures += "Missing $agentsPath"
} elseif ((Get-Content $agentsPath -Raw).Trim().Length -eq 0) {
    $failures += "$agentsPath is empty"
}

$claudePath = Join-Path $RepoRoot "CLAUDE.md"
if (-not (Test-Path $claudePath)) {
    $failures += "Missing $claudePath"
} elseif ((Get-Content $claudePath -Raw) -notmatch "AGENTS\.md") {
    $failures += "$claudePath does not reference AGENTS.md"
}

$geminiPath = Join-Path $RepoRoot "GEMINI.md"
if (-not (Test-Path $geminiPath)) {
    $failures += "Missing $geminiPath"
} elseif ((Get-Content $geminiPath -Raw) -notmatch "AGENTS\.md") {
    $failures += "$geminiPath does not reference AGENTS.md"
}

$readmePath = Join-Path $RepoRoot "README.md"
if (-not (Test-Path $readmePath)) {
    $failures += "Missing $readmePath"
}

if ($failures.Count -gt 0) {
    Write-Output "FAIL: pointer file verification"
    $failures | ForEach-Object { Write-Output "  - $_" }
    exit 1
}

Write-Output "PASS: AGENTS.md, CLAUDE.md, GEMINI.md, README.md all present and correctly linked"
exit 0
```

- [ ] **Step 2: Run it to verify it fails**

Run: `pwsh bootstrap/verify-pointers.ps1`
Expected: `FAIL: pointer file verification` listing all four missing files.

- [ ] **Step 3: Write the pointer files**

`AGENTS.md`:
```markdown
# agent-extensions

Kyle's personal cross-provider Claude Skills marketplace. Two plugins:

- `anthropic-product-skills` — Claude.ai product-surface skills (Canvas,
  Artifacts). Claude-only by nature; inert on Codex and Antigravity.
- `general-skills` — skills useful on any provider, starting with
  `skill-creator`.

## Adding a new skill

1. Decide which plugin it belongs to (or create a new themed plugin under
   `plugins/`, following the existing structure: `.claude-plugin/plugin.json`,
   `plugin.json`, `.codex-plugin/plugin.json`, `skills/`).
2. Add the skill folder under that plugin's `skills/`.
3. If vendored from elsewhere, add a line to that plugin's `skills/VENDORED-FROM`.
4. If it's a new plugin, add it to `.claude-plugin/marketplace.json`.
5. Run `bootstrap/sync.ps1` (or `sync.sh`) to re-link it into every provider.

See `docs/superpowers/specs/2026-08-20-agent-extensions-design.md` for the
full design rationale.
```

`CLAUDE.md`:
```markdown
See AGENTS.md.
```

`GEMINI.md`:
```markdown
@./AGENTS.md
```

`README.md`:
```markdown
# agent-extensions

A private, cross-provider Claude Skills marketplace for Claude Code, Codex,
and Antigravity. See [AGENTS.md](./AGENTS.md) for what's here and how to add
to it, and [the design spec](./docs/superpowers/specs/2026-08-20-agent-extensions-design.md)
for why it's built this way.

## Install

```bash
pwsh bootstrap/sync.ps1   # Windows
./bootstrap/sync.sh       # Unix / cloud containers
```
```

- [ ] **Step 4: Run the check to confirm it passes**

Run: `pwsh bootstrap/verify-pointers.ps1`
Expected: `PASS: AGENTS.md, CLAUDE.md, GEMINI.md, README.md all present and correctly linked`, exit code 0.

- [ ] **Step 5: Commit**

```bash
git add AGENTS.md CLAUDE.md GEMINI.md README.md bootstrap/verify-pointers.ps1
git commit -m "Add repo root pointer files (AGENTS.md, CLAUDE.md, GEMINI.md, README.md)"
```

---

## Task 4: sync.ps1 — Codex and Antigravity linking

**Files:**
- Create: `bootstrap/sync.ps1`
- Test: `bootstrap/sync.test.ps1`

**Interfaces:**
- Consumes: `plugins/*/skills/*` from Task 1, `plugins/*/plugin.json` (Antigravity manifest) from Task 2
- Produces: two functions later tasks and `sync.sh` mirror by name/behavior — `Sync-CodexSkills($RepoRoot, $CodexSkillsDir)` and `Sync-AntigravityPlugins($RepoRoot, $AntigravityPluginsDir)`, both returning `$true` on success, throwing on failure (never returning `$false` silently)

- [ ] **Step 1: Write the failing test**

Create `bootstrap/sync.test.ps1`:

```powershell
#!/usr/bin/env pwsh
# Exercises Sync-CodexSkills and Sync-AntigravityPlugins against a scratch
# directory tree — never touches the real ~/.agents or ~/.gemini.
$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $PSScriptRoot
. (Join-Path $PSScriptRoot "sync.ps1") -Import

$scratch = Join-Path ([System.IO.Path]::GetTempPath()) "agent-extensions-synctest-$(Get-Random)"
$codexDir = Join-Path $scratch "agents-skills"
$antigravityDir = Join-Path $scratch "gemini-plugins"
New-Item -ItemType Directory -Path $codexDir -Force | Out-Null
New-Item -ItemType Directory -Path $antigravityDir -Force | Out-Null

$failures = @()

# --- Codex linking ---
Sync-CodexSkills -RepoRoot $RepoRoot -CodexSkillsDir $codexDir

foreach ($skill in @("canvas-design", "web-artifacts-builder", "skill-creator")) {
    $linkPath = Join-Path $codexDir $skill
    $item = Get-Item $linkPath -ErrorAction SilentlyContinue
    if (-not $item) {
        $failures += "Codex: $linkPath does not exist"
    } elseif (-not $item.LinkType) {
        $failures += "Codex: $linkPath exists but is not a junction/link (would be a stale copy, not a live link)"
    }
}

# --- Idempotency: run again, expect no error and no duplicate/broken state ---
Sync-CodexSkills -RepoRoot $RepoRoot -CodexSkillsDir $codexDir
$recheck = Get-Item (Join-Path $codexDir "skill-creator") -ErrorAction SilentlyContinue
if (-not $recheck -or -not $recheck.LinkType) {
    $failures += "Codex: second sync run broke the skill-creator link"
}

# --- Antigravity linking ---
Sync-AntigravityPlugins -RepoRoot $RepoRoot -AntigravityPluginsDir $antigravityDir

foreach ($plugin in @("anthropic-product-skills", "general-skills")) {
    $linkPath = Join-Path $antigravityDir $plugin
    $item = Get-Item $linkPath -ErrorAction SilentlyContinue
    if (-not $item) {
        $failures += "Antigravity: $linkPath does not exist"
    } elseif (-not $item.LinkType) {
        $failures += "Antigravity: $linkPath exists but is not a junction/link"
    }
}

# --- Live-link requirement: editing the repo file must be visible through the link ---
$marker = "sync-test-marker-$(Get-Random)"
$testFile = Join-Path $RepoRoot "plugins\general-skills\skills\skill-creator\SKILL.md"
Add-Content -Path $testFile -Value "<!-- $marker -->"
$viaLink = Get-Content (Join-Path $codexDir "skill-creator\SKILL.md") -Raw
if ($viaLink -notmatch [regex]::Escape($marker)) {
    $failures += "Codex link for skill-creator is a copy, not a live link (marker not visible through it)"
}
(Get-Content $testFile) | Where-Object { $_ -notmatch [regex]::Escape($marker) } | Set-Content $testFile

Remove-Item -Recurse -Force $scratch

if ($failures.Count -gt 0) {
    Write-Output "FAIL: sync.ps1 Codex/Antigravity linking"
    $failures | ForEach-Object { Write-Output "  - $_" }
    exit 1
}

Write-Output "PASS: Codex and Antigravity linking, idempotent, live-linked"
exit 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pwsh bootstrap/sync.test.ps1`
Expected: FAIL — `sync.ps1` does not yet exist / does not export `Sync-CodexSkills` or `Sync-AntigravityPlugins`.

- [ ] **Step 3: Write minimal implementation**

Create `bootstrap/sync.ps1`:

```powershell
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
}

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
}

if ($Import) { return }

Sync-CodexSkills -RepoRoot $RepoRoot -CodexSkillsDir $CodexSkillsDir
Sync-AntigravityPlugins -RepoRoot $RepoRoot -AntigravityPluginsDir $AntigravityPluginsDir
if (-not $SkipClaudeCode) {
    Sync-ClaudeCodeMarketplace -RepoRoot $RepoRoot
}

Write-Output "Sync complete."
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pwsh bootstrap/sync.test.ps1`
Expected: `PASS: Codex and Antigravity linking, idempotent, live-linked`, exit code 0.

- [ ] **Step 5: Commit**

```bash
git add bootstrap/sync.ps1 bootstrap/sync.test.ps1
git commit -m "Add sync.ps1 Codex and Antigravity linking, with idempotent scratch-tested coverage"
```

---

## Task 5: sync.ps1 — Claude Code marketplace/plugin registration

**Files:**
- Modify: `bootstrap/sync.ps1` (already contains `Sync-ClaudeCodeMarketplace`, written in Task 4 — this task tests and hardens it)
- Test: `bootstrap/sync.claude-code.test.ps1`

**Interfaces:**
- Consumes: `Sync-ClaudeCodeMarketplace($RepoRoot)` from Task 4, `.claude-plugin/marketplace.json` from Task 2
- Produces: nothing new consumed by later tasks

This function registers a real marketplace and installs real plugins into Kyle's actual global Claude Code state — there is no per-invocation sandbox flag for `claude plugin marketplace add`. Unlike Task 4, this test is **not** scratch-isolated; it exercises the real `claude` CLI and asserts against its real output. This is a deliberate, stated tradeoff (see the spec's Verification section), not a weaker test dressed up as equivalent — it is still red-before-green, still automated, still asserts the spec's exact acceptance criterion.

- [ ] **Step 1: Write the failing test**

Create `bootstrap/sync.claude-code.test.ps1`:

```powershell
#!/usr/bin/env pwsh
# Exercises the real `claude` CLI against this repo. Not scratch-isolated —
# see Task 5's note in the plan for why that isn't possible for this part.
$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $PSScriptRoot
. (Join-Path $PSScriptRoot "sync.ps1") -Import

Sync-ClaudeCodeMarketplace -RepoRoot $RepoRoot

$listOutput = & claude plugin list 2>&1 | Out-String
$failures = @()

if ($listOutput -notmatch "anthropic-product-skills@agent-extensions") {
    $failures += "claude plugin list does not show anthropic-product-skills@agent-extensions"
}
if ($listOutput -notmatch "general-skills@agent-extensions") {
    $failures += "claude plugin list does not show general-skills@agent-extensions"
}

# Idempotency: re-run must not error and must still show both plugins
Sync-ClaudeCodeMarketplace -RepoRoot $RepoRoot
$listOutput2 = & claude plugin list 2>&1 | Out-String
if ($listOutput2 -notmatch "anthropic-product-skills@agent-extensions" -or
    $listOutput2 -notmatch "general-skills@agent-extensions") {
    $failures += "Second sync run lost a plugin registration"
}

if ($failures.Count -gt 0) {
    Write-Output "FAIL: sync.ps1 Claude Code registration"
    $failures | ForEach-Object { Write-Output "  - $_" }
    exit 1
}

Write-Output "PASS: Claude Code marketplace + both plugins registered and idempotent"
exit 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pwsh bootstrap/sync.claude-code.test.ps1`
Expected: FAIL — `claude plugin list` does not yet show either plugin (marketplace not yet registered on this machine).

- [ ] **Step 3: Confirm the implementation (already written in Task 4)**

`Sync-ClaudeCodeMarketplace` already exists in `bootstrap/sync.ps1` from Task 4, Step 3. No new code — this step is to re-read it against this task's stricter test before running.

- [ ] **Step 4: Run test to verify it passes**

Run: `pwsh bootstrap/sync.claude-code.test.ps1`
Expected: `PASS: Claude Code marketplace + both plugins registered and idempotent`, exit code 0.

- [ ] **Step 5: Commit**

```bash
git add bootstrap/sync.claude-code.test.ps1
git commit -m "Add live-CLI test coverage for sync.ps1 Claude Code registration"
```

---

## Task 6: sync.sh — Unix/cloud-container parity

**Files:**
- Create: `bootstrap/sync.sh`
- Test: `bootstrap/sync.test.sh`

**Interfaces:**
- Consumes: same plugin/skill layout as Task 4/5
- Produces: `sync_codex_skills`, `sync_antigravity_plugins`, `sync_claude_code_marketplace` shell functions — same three responsibilities as the PowerShell functions, symlinks instead of junctions

- [ ] **Step 1: Write the failing test**

Create `bootstrap/sync.test.sh`:

```bash
#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SCRATCH="$(mktemp -d)"
CODEX_DIR="$SCRATCH/agents-skills"
ANTIGRAVITY_DIR="$SCRATCH/gemini-plugins"
mkdir -p "$CODEX_DIR" "$ANTIGRAVITY_DIR"

# shellcheck source=/dev/null
source "$REPO_ROOT/bootstrap/sync.sh" --import

failures=()

sync_codex_skills "$REPO_ROOT" "$CODEX_DIR"
for skill in canvas-design web-artifacts-builder skill-creator; do
  if [ ! -e "$CODEX_DIR/$skill" ]; then
    failures+=("Codex: $CODEX_DIR/$skill does not exist")
  elif [ ! -L "$CODEX_DIR/$skill" ]; then
    failures+=("Codex: $CODEX_DIR/$skill exists but is not a symlink")
  fi
done

# Idempotency
sync_codex_skills "$REPO_ROOT" "$CODEX_DIR"
if [ ! -L "$CODEX_DIR/skill-creator" ]; then
  failures+=("Codex: second sync run broke the skill-creator link")
fi

sync_antigravity_plugins "$REPO_ROOT" "$ANTIGRAVITY_DIR"
for plugin in anthropic-product-skills general-skills; do
  if [ ! -e "$ANTIGRAVITY_DIR/$plugin" ]; then
    failures+=("Antigravity: $ANTIGRAVITY_DIR/$plugin does not exist")
  elif [ ! -L "$ANTIGRAVITY_DIR/$plugin" ]; then
    failures+=("Antigravity: $ANTIGRAVITY_DIR/$plugin exists but is not a symlink")
  fi
done

# Live-link requirement
MARKER="sync-test-marker-$$"
TEST_FILE="$REPO_ROOT/plugins/general-skills/skills/skill-creator/SKILL.md"
echo "<!-- $MARKER -->" >> "$TEST_FILE"
if ! grep -q "$MARKER" "$CODEX_DIR/skill-creator/SKILL.md"; then
  failures+=("Codex link for skill-creator is a copy, not a live link")
fi
sed -i "/$MARKER/d" "$TEST_FILE"

rm -rf "$SCRATCH"

if [ ${#failures[@]} -gt 0 ]; then
  echo "FAIL: sync.sh Codex/Antigravity linking"
  printf '  - %s\n' "${failures[@]}"
  exit 1
fi

echo "PASS: Codex and Antigravity linking, idempotent, live-linked"
exit 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `bash bootstrap/sync.test.sh`
Expected: FAIL — `bootstrap/sync.sh` does not yet exist.

- [ ] **Step 3: Write minimal implementation**

Create `bootstrap/sync.sh`:

```bash
#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CODEX_SKILLS_DIR="${CODEX_SKILLS_DIR:-$HOME/.agents/skills}"
ANTIGRAVITY_PLUGINS_DIR="${ANTIGRAVITY_PLUGINS_DIR:-$HOME/.gemini/config/plugins}"
SKIP_CLAUDE_CODE="${SKIP_CLAUDE_CODE:-}"

get_plugin_names() {
  local repo_root="$1"
  find "$repo_root/plugins" -mindepth 1 -maxdepth 1 -type d -exec basename {} \;
}

new_or_repair_symlink() {
  local link_path="$1"
  local target_path="$2"

  if [ -e "$link_path" ] || [ -L "$link_path" ]; then
    if [ ! -L "$link_path" ]; then
      echo "Refusing to overwrite '$link_path' — it exists and is not a link this script manages." >&2
      return 1
    fi
    local current_target
    current_target="$(readlink "$link_path")"
    if [ "$current_target" = "$target_path" ]; then
      return 0  # already correct, idempotent no-op
    fi
    rm "$link_path"
  fi

  if ! ln -s "$target_path" "$link_path"; then
    echo "Failed to create symlink '$link_path' -> '$target_path'" >&2
    return 1
  fi
}

sync_codex_skills() {
  local repo_root="$1"
  local codex_skills_dir="$2"
  mkdir -p "$codex_skills_dir"

  local plugin skills_root
  while IFS= read -r plugin; do
    skills_root="$repo_root/plugins/$plugin/skills"
    [ -d "$skills_root" ] || continue
    local skill_dir skill_name
    for skill_dir in "$skills_root"/*/; do
      [ -d "$skill_dir" ] || continue
      skill_name="$(basename "$skill_dir")"
      new_or_repair_symlink "$codex_skills_dir/$skill_name" "${skill_dir%/}"
    done
  done < <(get_plugin_names "$repo_root")
}

sync_antigravity_plugins() {
  local repo_root="$1"
  local antigravity_plugins_dir="$2"
  mkdir -p "$antigravity_plugins_dir"

  local plugin
  while IFS= read -r plugin; do
    new_or_repair_symlink "$antigravity_plugins_dir/$plugin" "$repo_root/plugins/$plugin"
  done < <(get_plugin_names "$repo_root")
}

sync_claude_code_marketplace() {
  local repo_root="$1"

  claude plugin marketplace add "$repo_root"

  local plugin
  while IFS= read -r plugin; do
    claude plugin install "$plugin@agent-extensions"
  done < <(get_plugin_names "$repo_root")
}

if [ "${1:-}" = "--import" ]; then
  return 0 2>/dev/null || exit 0
fi

sync_codex_skills "$REPO_ROOT" "$CODEX_SKILLS_DIR"
sync_antigravity_plugins "$REPO_ROOT" "$ANTIGRAVITY_PLUGINS_DIR"
if [ -z "$SKIP_CLAUDE_CODE" ]; then
  sync_claude_code_marketplace "$REPO_ROOT"
fi

echo "Sync complete."
```

- [ ] **Step 4: Run test to verify it passes**

Run: `bash bootstrap/sync.test.sh`
Expected: `PASS: Codex and Antigravity linking, idempotent, live-linked`, exit code 0.

- [ ] **Step 5: Commit**

```bash
git add bootstrap/sync.sh bootstrap/sync.test.sh
git commit -m "Add sync.sh Unix/cloud-container parity for Codex and Antigravity linking"
```

---

## Task 7: End-to-end verification and GitHub push

**Files:**
- Create: `.gitignore` (exclude nothing repo-specific yet, but standard OS/editor noise: `.DS_Store`, `Thumbs.db`, `*.swp`)
- No other file changes — this task runs what already exists and publishes it

**Interfaces:**
- Consumes: every artifact from Tasks 1–6
- Produces: the pushed GitHub repo itself

- [ ] **Step 1: Write the failing check**

There is no new code to test here — the "test" is the full local verification suite plus a real push, run in order. The failing state is simply: `kgsmith19/agent-extensions` does not exist on GitHub yet.

Run: `gh repo view kgsmith19/agent-extensions`
Expected: FAIL — `Could not resolve to a Repository`.

- [ ] **Step 2: Run the full local verification suite**

```bash
pwsh bootstrap/verify-content.ps1
pwsh bootstrap/verify-pointers.ps1
pwsh bootstrap/sync.test.ps1
pwsh bootstrap/sync.claude-code.test.ps1
bash bootstrap/sync.test.sh
```

Expected: every one prints its `PASS:` line and exits 0. If any fails, stop and fix before proceeding — do not push a repo whose own verification suite is red.

- [ ] **Step 3: Add .gitignore and commit remaining files**

Create `.gitignore`:
```
.DS_Store
Thumbs.db
*.swp
```

```bash
git add .gitignore
git commit -m "Add .gitignore"
```

- [ ] **Step 4: Create the private GitHub repo and push**

```bash
gh repo create kgsmith19/agent-extensions --private --source=. --remote=origin
git push -u origin main
```

- [ ] **Step 5: Verify the push**

Run: `gh repo view kgsmith19/agent-extensions`
Expected: PASS — repo details print, `Private` visibility, default branch `main`.

Run: `git log --oneline origin/main -1`
Expected: matches the local `HEAD` commit from Step 3.

- [ ] **Step 6: Run the real sync once more against the pushed remote**

```bash
pwsh bootstrap/sync.ps1
```

Expected: `Sync complete.` — this is the first real, non-scratch, non-test run: Codex and Antigravity get real junctions on this machine, Claude Code gets the marketplace and both plugins registered for real.

- [ ] **Step 7: Final commit (if Step 6 changed anything tracked — normally it changes only machine-local state, so this step is a no-op check)**

Run: `git status --short`
Expected: clean — `sync.ps1` only ever writes outside the repo (junctions in `~/.agents/skills`, `~/.gemini/config/plugins`, and Claude Code's own global plugin state), so nothing here should be untracked or modified.

---

## Self-Review Notes

- **Spec coverage:** every spec section maps to a task — Problem/Scope/Non-goals → plan header and Global Constraints; Architecture → Tasks 1–2; Sync mechanism → Tasks 4–6; Distribution → Task 7; Verification criteria 1–5 → `sync.claude-code.test.ps1` (criterion 1), `sync.test.ps1`/`sync.test.sh` (criteria 2–4), `verify-content.ps1` (criterion 5).
- **Placeholder scan:** no TBD/TODO; every step has real, complete code.
- **Type/name consistency:** `Sync-CodexSkills`, `Sync-AntigravityPlugins`, `Sync-ClaudeCodeMarketplace` (PowerShell) and `sync_codex_skills`, `sync_antigravity_plugins`, `sync_claude_code_marketplace` (bash) are each used identically across the task that defines them and the task that tests them — verified no drift between Task 4/5's PowerShell names and Task 6's bash names (same responsibilities, language-appropriate casing).
- **Plugin name consistency:** `anthropic-product-skills` and `general-skills` are the only two strings used across manifests, sync functions, and tests — no third variant introduced anywhere.

## Execution Notes

Tasks 1 and 3 have no dependency on each other and can run in parallel worktrees. Task 2 depends on Task 1 (validation needs real skill content present). Tasks 4 and 5 both depend on Task 2; Task 6 mirrors Tasks 4–5's proven behavior. Task 7 depends on everything. In practice: 2 parallel worktrees for {Task 1, Task 3}, then Tasks 2 → {4, 5} → 6 → 7 proceed once their dependencies land, each still getting its own fresh implementer/reviewer subagent pair per `subagent-driven-development`.
