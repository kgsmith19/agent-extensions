# One-line agent setup for any machine/harness (issue #53) — design

Date: 2026-09-23
Status: proposed — awaiting owner review
Issue: kgsmith19/agent-extensions#53 ("One-line agent setup for any machine/harness with gitignored local overlay")
Related: #50 (AGENTS.md canonical; thin provider pointers), #28 (harness-agnostic vision), #52 (AGENTS.local.md overlay injection, merged as 171e07e)

## Problem

`curl -fsSL .../bootstrap.sh | bash` today only clones to `~/.agent-extensions`
and runs capability-gated `sync.sh` (skills/plugins links). On a bare machine it
does NOT install:

1. global gitignore for local overlays (`AGENTS.local.md`, `AGENTS.override.md`)
   via `core.excludesFile`, so no repo ever shows them as untracked;
2. harness shims (`~/.pi/agent/AGENTS.md`, `~/.codex/AGENTS.md`,
   `~/.claude/CLAUDE.md`, `~/.gemini/GEMINI.md` pointing at the baseline);
3. continuity adapters wired (pi extension + Claude hooks) idempotently;
4. forceful repo-mode injection — a repo that opts in gets a gitignored
   `AGENTS.local.md` overlay, harness bindings, and compliance guards without
   any committed change to that repo.

This machine already has all of it, installed by hand. #53 makes the one-liner
the installer so every machine converges to the same state. Verified live on
this machine (2026-09-23): shims present with precedence chain
owner → project → `~/AGENTS.md` → harness config → harness defaults; Claude
`settings.json` hooks (`SessionStart`, `PreCompact`) call
`agent_extensions/continuity/adapters/claude_hook.py`; pi `settings.json`
`extensions` lists `adapters/pi-extension.ts`; global excludes file
`~/.config/git/ignore` carries the overlay entries.

## Goals

- One command gives any checkout on any machine (Windows Git Bash, mac/linux,
  thin cloud container) the full agent setup, committing nothing into any repo:
  baseline rules layer, capability bundle, harness shims, continuity wiring.
- An everyday command (`ae`, name overridable): after first bootstrap,
  `ae update` grabs the latest agent-extensions in any environment (clone if
  absent, fast-forward if present) and re-converges; `ae init` / `ae status`
  work from any repo.
- Repo-mode (`init` inside a target repo) forcefully injects gitignored life:
  overlay + harness bindings + compliance guards. Additive only, never touches
  the repo's committed files.
- One source: `agent-extensions` houses everything. Python
  `agent_extensions/sync/bootstrap.py` stage semantics are the canonical
  implementer; shell stays a thin fetcher with zero logic.
- Provider-agnostic, machine-agnostic, env-agnostic: detection picks *which*
  stages apply, never *what* they do. No provider/model/env branching in core.
- Easy to customize, well structured: templates versioned in-repo, local
  fragments override without forking, `status` shows what is installed where.

## Non-goals (stays honest to #53)

- No `standard.lock` adoption receipt — that is the agent-engineering-standard
  repo's tool (`python tools/standardctl.py init`); this repo must not mint it.
- No CI workflow changes in this repo (no `.github/workflows` here by design).
- No new secret handling: only the machine identity needed to read the secret
  provider may remain in local plaintext (global rule). Installer never writes,
  prints, or duplicates secret values; rendered manifests are secret-scanned
  (existing `_check_no_secrets` semantics carry over).
- No per-repo committed changes, ever. Acceptance box "invisible to git status"
  is a test, not a hope.

## Global constraints (from ~/AGENTS.md + config.yaml, binding)

- Risk tier: R2 (machine-wide installer; a clobber bug destroys hand-written
  harness config — hence marker-guard below). No destructive write without an
  explicit marker or explicit owner flag.
- Python 3.12, stdlib only, no new dependencies (matches `sync/bootstrap.py`).
- Stage semantics reused verbatim: `applied | skipped | failed`, never silent,
  failures never stop siblings; reruns converge (link/shim already-correct =
  no-op). Report printed per stage with reasons.
- Test-first (TDD): every stage gets a failing test before implementation;
  full suite (`pytest -q`, 233 passing at 171e07e) stays green.
- Thin branch, ready PR with `Closes #53`; never commit to main; work in
  `.worktrees/issue-53-one-line-agent-setup`.

## Architecture — Approach A (Python-canonical installer)

New `agent_extensions/install/` package, sibling of `sync/`. `bootstrap.sh`
stays a thin fetcher: clone/update `~/.agent-extensions`, then
`python3 -m agent_extensions.install bootstrap`. All new logic lives in tested
Python; shell keeps zero logic.

Rejected: B (extend 60KB×2 shell twins `sync.sh`/`sync.ps1` in parallel —
drifts silently, opposite of "organized"); C (separate install.sh/install.ps1
— splits machine-setup from the repo that houses it).

```
agent_extensions/
  install/            # NEW, canonical
    __init__.py
    __main__.py       # CLI: bootstrap | update | init [repo] | status
    machine.py        # stages: gitignore, shims, hooks, then sync delegation
    repo.py           # stages: overlay, bindings, guards (repo-mode only)
    guards.py         # compliance guard script (secrets, protected-branch)
    templates/        # ONE versioned file per shim + overlay template
      pi-AGENTS.md
      claude-CLAUDE.md
      codex-AGENTS.md
      gemini-GEMINI.md
      AGENTS.local.md      # overlay template (bounded 32 KiB rendered)
      settings-local.json  # .claude/settings.local.json template
      ae-launcher.sh|ps1   # rendered to ~/bin/<AE_COMMAND|ae>[.ps1]
  sync/               # UNCHANGED: capability bundle stages (detect→…→read-back)
bootstrap.sh          # thin: clone/update → python -m agent_extensions.install bootstrap
```

`bootstrap.sh` keeps its two entry modes (in-checkout exec of local source;
bare-machine clone to `$AGENT_EXTENSIONS_DIR` default `~/.agent-extensions`,
overridable via `AGENT_EXTENSIONS_REPO_URL` / `AGENT_EXTENSIONS_DIR`) and
delegates to Python instead of `sync.sh`. `sync.sh`/`sync.ps1` remain as the
legacy capability path until the Python path proves parity, then thin to
wrappers — decided at plan time, not in this spec.

## Machine stages (the one-liner, idempotent, capability-gated)

Run order: `gitignore → shims → hooks → cli-shim → sync…` (existing sync stages
delegated, not duplicated). Each reports applied/skipped/failed with a reason.

The same entrypoint is the everyday command: `ae update` ensures the checkout
(clone on a bare machine), fast-forwards it (`git pull --ff-only`), and re-runs
the bootstrap stages to converge. A dirty or diverged checkout is a failed
stage naming the exact resolution commands — the updater never stashes,
resets, or discards local state.

1. **gitignore** — ensure `git config --global core.excludesFile` points at the
   platform excludes file (`~/.config/git/ignore`); ensure it contains exactly
   the overlay entries (`AGENTS.local.md`, `AGENTS.override.md`, plus the
   existing `**/.claude/settings.local.json` convention). Additive only:
   appends missing lines, never removes or rewrites user lines. Missing `git`
   binary → skipped with reason (thin containers without git still get the
   rest).
2. **shims** — render pi/claude/codex/gemini (+ kilo HARNESS.md where the kilo
   config dir exists) shims from `templates/`. Marker-guard (the force/safety
   contract): a file carrying our `managed-by: agent-extensions` marker is
   re-rendered when the template is newer; a file *without* it is skipped
   loudly (reported, never clobbered). Missing parent harness dir (e.g. no
   `~/.codex` on a Claude-only box) → that shim skipped with reason.
   "Forceful" means every covered surface converges; it never means overwriting
   hand-written config.
3. **hooks** — JSON-merge continuity adapters idempotently: Claude
   `~/.claude/settings.json` `SessionStart` + `PreCompact` entries pointing at
   the repo's `claude_hook.py` (dedupe by command string; preserve all other
   keys); pi `~/.pi/agent/settings.json` `extensions` array entry for
   `pi-extension.ts` (dedupe; preserve rest). Corrupt JSON → failed stage with
   path named, sibling stages unaffected.
4. **cli-shim** — render the everyday launcher: `ae` (bash) into `~/bin` and,
   when PowerShell is detected, `ae.ps1` beside it. Launcher resolves the
   checkout via `AGENT_EXTENSIONS_DIR` (default `~/.agent-extensions`) and
   execs `python3 -m agent_extensions.install "$@"`. Capability-gated: only
   when the target bin dir exists or can be created on PATH; otherwise
   skipped with the exact PATH line to add. Marker-guarded, idempotent;
   command name overridable via `AE_COMMAND`.
5. **sync** — delegate to existing `sync/bootstrap.py::bootstrap()` for the
   capability bundle (detect→locks→catalog→profile→render→link→read-back).
   No logic copied.

## Repo-mode stages (the "force" — `init` inside a target repo)

Runs only when invoked inside a repo (`git rev-parse --show-toplevel` must
succeed, else the stage fails loudly). Everything lands in gitignored paths by
construction; a post-run `git status --porcelain` cleanliness check is part of
the report.

1. **overlay** — render `AGENTS.local.md` from template + optional
   `AGENTS.local.d/*.md` fragments (lexical order, each fragment headed).
   Content: precedence chain, verification-before-completion,
   secrets/authority rules, worktree discipline — the baseline rules that make
   agents obey without duplicating project instructions. Bounded 32 KiB
   rendered (existing #52 bound honored). Existing `AGENTS.local.md` without
   our marker → skipped loudly, never overwritten.
2. **bindings** — `.claude/settings.local.json` (SessionStart/PreCompact
   capsule hooks pointing at the installed checkout's `claude_hook.py` via
   `$AGENT_EXTENSIONS_DIR`-relative resolution, plus PreToolUse guard entry)
   and the pi project-level extension pointer (exact settings key verified
   against pi docs at plan time — this spec does not guess it). JSON-merge,
   never clobber; both paths are in the global excludes list so `git status`
   stays clean.
3. **guards** — compliance guard script (from `guards.py`, installed
   gitignored next to the bindings, e.g. `.claude/agent-guard.py`): blocks
   secret-pattern writes (key-shaped values to chat/logs/files outside the
   approved stores) and protected-branch pushes (`main`/`master` direct push)
   at the tool layer with a message naming the violated rule and the
   allowed path. Deny-list patterns live in-repo and version with the
   installer; repo-local extra patterns via an optional gitignored config the
   guard reads if present. Guard failures are *blocking* (that is the point:
   compliance enforced, not suggested) but *narrow*: only the two classes
   above at v1; everything else stays advisory. Guard must fail closed to
   allow (a guard crash never blocks a session) and must never print secret
   values.

## Independence & customization

- No provider/model/env conditionals in core logic. Detection only selects
  applicable stages (harness dir present? writable? git present?). Adapters
  stay thin, same rule as continuity.
- Customization without forking: edit `templates/` (versioned, reviewed) or
  drop fragments in `AGENTS.local.d/`; point `AGENT_EXTENSIONS_DIR` at any
  checkout. `status` subcommand prints per-stage state (installed version vs
  template version, marker presence, hook registration) for machine and, when
  inside a repo, repo-mode.
- Paths resolve via `pathlib.Path.home()`; `~/.agent-extensions` default
  overridable by env. Windows Git Bash, mac/linux, and thin containers are the
  same code path with fewer detected surfaces — never special-cased logic.

## Acceptance → tests (each box is a test, tmp-HOME pattern per test_bootstrap.py)

- [ ] fresh-machine: tmp HOME, one `bootstrap` → skills linked, all
  applicable shims present with marker, hooks registered, sync stages applied.
- [ ] overlay-invisible: fresh repo with no `.gitignore`, repo-mode `init` →
  `AGENTS.local.md` + bindings present and `git status --porcelain` empty.
- [ ] layering: repo `AGENTS.md` untouched (byte-identical); shim files contain
  the precedence pointer to baseline, not duplicated rule text.
- [ ] idempotent: second `bootstrap`/`init` → identical report, zero writes
  (mtime-stable), exit zero.
- [ ] clobber-guard: pre-existing unmarked shim/overlay/settings file →
  skipped with reason, content byte-identical.
- [ ] guard-blocks: secret-pattern write attempt and protected-branch push
  command both denied with rule-naming message; guard crash (simulated) fails
  open to allow.
- [ ] never-in-repo: installer writes zero paths outside HOME global config
  and the target repo's gitignored overlay paths; suite stays green.
- [ ] cli-shim: after bootstrap, `ae` resolves on PATH and `ae status` exits
  zero printing per-stage state for machine and, inside a repo, repo-mode.
- [ ] update: bare tmp HOME, `ae update` → clone + full converge; after a new
  upstream commit, second `ae update` → fast-forward + converge; dirty
  checkout → failed stage with resolution guidance, zero data loss.

## Sequencing (for the plan, not this spec)

Machine stages first (gitignore, shims, hooks, cli-shim — they unblock every
machine), then repo-mode, then guard hardening, then `bootstrap.sh` delegation
cutover with legacy-shell parity proof. The `update` subcommand lands with the
CLI entry itself so every later task is exercisable via `ae`. Each lands as its own TDD task with the tests above.

## Risks

- Pi project-level extension key guessed wrong → mitigated by verifying
  against pi docs/package at plan time before writing the bindings stage.
- JSON-merge corrupting hand-written settings → mitigated by marker-guard +
  parse-validate-write (write only on successful re-parse) + backup of
  pre-existing files to the agents backup dir before first managed write.
- Guard over-blocking real work → mitigated by narrow v1 deny classes and
  fail-open on crash; over-block reports come back as issues, not silent
  workarounds.
- Shell/Python parity drift during transition → mitigated by delegating, not
  duplicating, and proving legacy tests still pass against the new entrypoint.
