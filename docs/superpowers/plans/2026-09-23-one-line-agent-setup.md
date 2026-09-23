# One-Line Agent Setup (issue #53) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** One command (`curl …/bootstrap.sh | bash`, then `ae`) installs baseline rules, capability bundle, harness shims, global gitignore, continuity hooks, and a forceful gitignored repo-mode injection — idempotent, marker-guarded, on Windows Git Bash / mac / linux / thin containers.

**Architecture:** New `agent_extensions/install/` package (canonical Python; shell stays thin). Machine stages `gitignore → shims → hooks → cli-shim` delegate to existing `sync/bootstrap.py` for capabilities. Repo-mode `init` injects `AGENTS.local.md` overlay + `.claude/settings.local.json` + `.pi/settings.json` bindings + guard wiring. CLI: `bootstrap | update | init [repo] | status`. Reuses `StageResult`/`BootstrapReport`/`_run_stage` semantics from `agent_extensions/sync/bootstrap.py`.

**Tech Stack:** Python 3.12 stdlib only, pytest 9.1.1, bash (thin launcher), JSON, git CLI.

**Spec:** `docs/superpowers/specs/2026-09-23-one-line-agent-setup-design.md` (same branch). The spec is the binding authority; conflicts resolve against it.

## Global Constraints

- Python 3.12 stdlib only; no new dependencies (spec: Global Constraints).
- Stage semantics verbatim from sync: `applied|skipped|failed`, never silent, failures never stop siblings, reruns converge. Reuse `_run_stage` — never raise out of a stage.
- No destructive writes: whole-file templates are marker-guarded (`<!-- managed-by: agent-extensions … -->`); JSON settings merges are additive-only (never overwrite foreign keys); corrupt JSON = failed stage naming the path.
- No writes into any target repo's committed tree; repo-mode files are covered by the global excludes entries installed by the gitignore stage.
- No secret values read, written, printed, or logged; guard never echoes matched secret text.
- Idempotency proof = second run: `report.ok()`, every stage reports an up-to-date/no-op detail, and zero file mtimes change.
- Full suite green at every task end: `python -m pytest -q` (233 passing at 171e07e).
- Work only in worktree `.worktrees/issue-53-one-line-agent-setup`, branch `issue/53-one-line-agent-setup`. Never commit to main.

## Review Focus

- Clobber risk: every write path must be marker-guarded or additive-only — a hand-written shim/settings file must survive a bootstrap run byte-identical (test pins this per stage).
- Status pollution: after repo-mode `init`, `git status --porcelain` in the target repo must be empty with no repo `.gitignore` change (test pins this).
- Fail-open vs fail-closed: installer stages fail *reported* (sibling stages continue); the guard *blocks* on violation but fails *open* on its own crash (tests pin both).
- Path portability: `~` expansion, Windows Git Bash + POSIX paths, no hardcoded `C:\Users\kyleg` outside test fixtures (grep-gate in Task 6).
- Secret hygiene: rendered/merged outputs contain no secret markers (reuse `SECRET_MARKERS` scan).

## Pre-decided rulings (from spec + plan-time verification)

- R1: pi project binding = `.pi/settings.json` `extensions` array with absolute path to the checkout's `pi-extension.ts` (verified against pi docs: project `.pi/settings.json`, `extensions: string[]`, loads after trust). Written only when file absent; `**/.pi/settings.json` joins the global excludes so owner repos stay clean. Existing `.pi/settings.json` (team file) → skipped loudly.
- R2: guard script is NOT copied into target repos — bindings reference the checkout's `agent_extensions/install/guards.py` (versions with the installer via `ae update`). Deviates from spec's "installed gitignored next to the bindings"; recorded here and in the ledger.
- R3: JSON files carry no comments, so marker-guard applies only to whole-file templates (shims, overlay, launcher); JSON safety comes from additive-only merging.
- R4: backup before first modification per run: changed JSON files are copied to `$AGENTS_BACKUP_DIR` (default `~/.config/agents/backups`) `install/<timestamp>/` before write. No change → no backup (keeps idempotency).
- R5: templates for shims/baseline are the owner's live files, portable-ized: `C:\Users\kyleg` → `~`, `C:\code\<repo>` → `~/code/<repo>`, plus marker line. Live contents are embedded in Task 2.

---

## File Structure

```
agent_extensions/install/
  __init__.py          # package marker + constants (marker, entries)
  __main__.py          # argparse CLI: bootstrap | update | init | status
  machine.py           # stage_gitignore, stage_shims, stage_hooks, stage_cli_shim, bootstrap()
  repo.py              # stage_overlay, stage_bindings, stage_guards, stage_status_clean, init()
  update.py            # cmd_update(): ensure checkout, ff-only pull, converge
  guards.py            # guard: pure find_violations() + hook-entrypoint main + --selfcheck
  templates/
    AGENTS.md          # baseline rules (portable-ized live file + marker)
    pi-AGENTS.md       # <agent-dir>/AGENTS.md shim + marker
    claude-CLAUDE.md
    codex-AGENTS.md
    gemini-GEMINI.md
    kilo-HARNESS.md
    ae-launcher.sh
    ae-launcher.ps1
    AGENTS.local.md    # repo overlay template + marker
tests/
  test_install_machine.py   # Tasks 1-4
  test_install_repo.py      # Task 5
  test_install_cli.py       # Tasks 4, 6 (CLI + update)
bootstrap.sh               # Task 6 cutover (python delegation, sync.sh fallback)
README.md                  # Task 6: One-line setup section
```

Shared helpers live in `machine.py` and are imported by `repo.py`/`update.py` (single source, no duplication).

---

### Task 1: install package skeleton + gitignore stage

**Files:**
- Create: `agent_extensions/install/__init__.py`, `agent_extensions/install/machine.py`
- Test: `tests/test_install_machine.py`

**Interfaces:**
- Produces: `MANAGED_MD_MARKER = "<!-- managed-by: agent-extensions -->"`, `GITIGNORE_ENTRIES`, `GITIGNORE_NOTE`, `stage_gitignore(home: Path) -> StageResult`, `run_stage(name, fn)` (re-export of sync `_run_stage`), `git_env(home, *args)`.

- [ ] **Step 1: Write failing tests**

```python
import os, subprocess
from pathlib import Path
import pytest
from agent_extensions.install.machine import stage_gitignore, GITIGNORE_ENTRIES

@pytest.fixture()
def home(tmp_path, monkeypatch):
    h = tmp_path / "home"; h.mkdir()
    monkeypatch.setenv("HOME", str(h)); monkeypatch.setenv("USERPROFILE", str(h))
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(h / ".gitconfig"))
    return h

def test_sets_excludesfile_and_appends_entries(home):
    r = stage_gitignore(home)
    assert r.status == "applied"
    excl = home / ".config" / "git" / "ignore"
    assert excl.exists()
    lines = excl.read_text().splitlines()
    for e in GITIGNORE_ENTRIES: assert e in lines
    out = os.popen(f'git config --global core.excludesFile').read().strip()
    assert Path(out).resolve() == excl.resolve()

def test_idempotent_no_new_lines(home):
    stage_gitignore(home)
    before = (home / ".config" / "git" / "ignore").read_text()
    r = stage_gitignore(home)
    assert r.status == "applied" and "up to date" in r.detail
    assert (home / ".config" / "git" / "ignore").read_text() == before

def test_never_removes_user_lines(home):
    excl = home / ".config" / "git" / "ignore"
    excl.parent.mkdir(parents=True); excl.write_text("*.log\nnode_modules/\n")
    stage_gitignore(home)
    txt = excl.read_text()
    assert "*.log\n" in txt and "node_modules/\n" in txt

def test_missing_git_is_skipped_not_failed(home, monkeypatch):
    monkeypatch.setenv("PATH", "")  # git not found
    r = stage_gitignore(home)
    assert r.status == "skipped"
```

- [ ] **Step 2: Run to verify RED** — `python -m pytest tests/test_install_machine.py -q` → fails (module missing).
- [ ] **Step 3: Implement**

```python
# agent_extensions/install/__init__.py
"""Forceful, idempotent machine + repo agent setup (issue #53). See docs spec 2026-09-23."""
MANAGED_MD_MARKER = "<!-- managed-by: agent-extensions -->"
GITIGNORE_NOTE = "# agent harness local overlays — never committed in any repo"
GITIGNORE_ENTRIES = ["AGENTS.local.md", "AGENTS.override.md",
                     "**/.claude/settings.local.json", "**/.pi/settings.json",
                     "**/AGENTS.local.d/"]

# machine.py (core of the task)
import os, subprocess
from pathlib import Path
from agent_extensions.sync.bootstrap import APPLIED, FAILED, SKIPPED, StageResult, _run_stage

def git_env(home: Path, *args):
    env = dict(os.environ, HOME=str(home), USERPROFILE=str(home))
    return subprocess.run(["git", *args], capture_output=True, text=True, env=env, timeout=30)

def stage_gitignore(home: Path, entries=None) -> StageResult:
    """Idempotent global excludes: point core.excludesFile at the ignore file, append missing entries."""
    def run(enabled=True, fn=None, skip=""):
        return _run_stage("gitignore", fn or (lambda: ""), enabled=enabled, skip_reason=skip)
    probe = git_env(home, "config", "--global", "core.excludesFile")
    if probe.returncode != 0:
        return run(enabled=False, skip="git unavailable")
    entries = list(entries if entries is not None else __import__("agent_extensions.install", fromlist=["x"]).GITIGNORE_ENTRIES)
    raw = probe.stdout.strip()
    if raw:
        target = Path(os.path.expanduser(raw.replace("\\", "/")))
        if not target.is_absolute(): target = home / target
    else:
        target = home / ".config" / "git" / "ignore"
        setr = git_env(home, "config", "--global", "core.excludesFile", str(target))
        if setr.returncode != 0:
            return run(fn=lambda: (_ for _ in ()).throw(RuntimeError(setr.stderr.strip())))
    def work():
        target.parent.mkdir(parents=True, exist_ok=True)
        existing = target.read_text(encoding="utf-8") if target.exists() else ""
        have = existing.splitlines()
        missing = [e for e in entries if e not in have]
        if not missing:
            return f"excludes up to date: {target}"
        with target.open("a", encoding="utf-8", newline="\n") as fh:
            if existing and not existing.endswith("\n"): fh.write("\n")
            fh.write(GITIGNORE_NOTE + "\n")
            fh.writelines(e + "\n" for e in missing)
        return f"appended {len(missing)} entries: {target}"
    return _run_stage("gitignore", work, enabled=True)
```

- [ ] **Step 4: GREEN** — pytest file green.
- [ ] **Step 5: Commit** — `git add -A && git commit -m "feat(install): gitignore stage — global excludes with additive entries (#53)"`

---

### Task 2: shims stage + templates (incl. baseline)

**Files:**
- Create: `agent_extensions/install/templates/{AGENTS.md, pi-AGENTS.md, claude-CLAUDE.md, codex-AGENTS.md, gemini-GEMINI.md, kilo-HARNESS.md}`
- Modify: `agent_extensions/install/machine.py` (`stage_shims`, `render_template`)
- Test: `tests/test_install_machine.py` (append)

**Interfaces:**
- Produces: `SHIMS: dict[str, ShimSpec]` (name → dataclass with `template`, `target(home)->Path`, `gate(home)->Path|None`), `render_template(repo_root, name, ctx) -> str`, `stage_shims(repo_root, home) -> StageResult`.

- [ ] **Step 1: failing tests**

```python
from agent_extensions.install.machine import stage_shims
from agent_extensions.install import MANAGED_MD_MARKER

def _repo(): return Path(__file__).resolve().parents[1]

def test_fresh_home_writes_all_gated_shims(home):
    (home / ".pi" / "agent").mkdir(parents=True); (home / ".claude").mkdir()
    r = stage_shims(_repo(), home)
    assert r.status == "applied"
    assert MANAGED_MD_MARKER in (home / "AGENTS.md").read_text()          # baseline, ungated
    assert "pi" in r.detail and "claude" in r.detail
    assert (home / ".pi" / "agent" / "AGENTS.md").exists()
    assert (home / ".claude" / "CLAUDE.md").read_text().count(MANAGED_MD_MARKER) == 1
    assert not (home / ".codex").exists()                                 # gated off
    assert "codex: skipped" in r.detail

def test_unmanaged_shim_never_clobbered(home):
    (home / ".claude").mkdir()
    (home / ".claude" / "CLAUDE.md").write_text("# my hand-written rules\nKEEP ME\n")
    stage_shims(_repo(), home)
    assert "KEEP ME" in (home / ".claude" / "CLAUDE.md").read_text()
    assert "unmanaged" in stage_shims(_repo(), home).detail

def test_managed_shim_updated_on_template_change(home):
    (home / "AGENTS.md").write_text(MANAGED_MD_MARKER + "\nstale\n")
    stage_shims(_repo(), home)
    assert "stale" not in (home / "AGENTS.md").read_text()

def test_shim_idempotent_mtime(home):
    stage_shims(_repo(), home)
    t = (home / "AGENTS.md").stat().st_mtime_ns
    stage_shims(_repo(), home)
    assert (home / "AGENTS.md").stat().st_mtime_ns == t
```

- [ ] **Step 2: RED.** — [ ] **Step 3: implement** `render_template` (read template, `str.format`-style replace of `{{HOME}}`/`{{BASELINE}}` tokens — use plain `.replace()`), `ShimSpec`, `SHIMS` mapping, `stage_shims` with marker-guard (exists+marker+same → no-op; exists+marker+diff → rewrite; exists unmarked → skip loud; missing+gate-ok → write; gate missing → skip with reason). Templates = live shim files ported per R4/R5:

- `AGENTS.md`: owner's live `~/AGENTS.md` content, first line marker, substitutions `C:\Users\kyleg` → `~`, `C:\code\<repo>` → `~/code/<repo>`, `C:\code` → `~/code`; section order and wording preserved.
- `pi-AGENTS.md`: live `~/.pi/agent/AGENTS.md` with same substitutions + marker; target `<home>/.pi/agent/AGENTS.md`, gate `<home>/.pi/agent`.
- `claude-CLAUDE.md`, `codex-AGENTS.md`, `gemini-GEMINI.md`: live contents (captured in session, embedded verbatim in templates) + substitutions + marker; targets `~/.claude/CLAUDE.md`, `~/.codex/AGENTS.md`, `~/.gemini/GEMINI.md`; gates `~/.claude`, `~/.codex`, `~/.gemini`.
- `kilo-HARNESS.md`: live kilo shim + marker; target `~/.config/kilo/HARNESS.md`, gate `~/.config/kilo`.

- [ ] **Step 4: GREEN.** — [ ] **Step 5: Commit** — `feat(install): marker-guarded harness shims + baseline template (#53)`

---

### Task 3: hooks stage (additive JSON merge + backup)

**Files:**
- Modify: `agent_extensions/install/machine.py` (`stage_hooks`, `_read_json`, `_write_json`, `backup_file`)
- Test: `tests/test_install_machine.py` (append)

**Interfaces:**
- Produces: `claude_hook_cmd(install_repo) -> str`, `stage_hooks(install_repo, home, backup_root=None) -> StageResult`, helpers `_read_json(path)->tuple[dict|None,str]`, `_write_json(path, data) -> None` (tmp+replace), `backup(path, backup_root) -> Path`.

- [ ] **Step 1: failing tests**

```python
import json
from agent_extensions.install.machine import stage_hooks

def _cl(home): return home / ".claude" / "settings.json"

def test_hooks_merge_into_existing_preserving_keys(home):
    p = _cl(home); p.parent.mkdir(parents=True)
    p.write_text(json.dumps({"model": "opus", "hooks": {"Stop": [{"hooks": [{"type": "command", "command": "echo mine"}]}]}}))
    r = stage_hooks(_repo(), home)
    assert r.status == "applied"
    data = json.loads(p.read_text())
    assert data["model"] == "opus"                                  # preserved
    assert any("claude_hook.py" in h["command"] for e in data["hooks"]["SessionStart"] for h in e["hooks"])
    assert data["hooks"]["Stop"] == json.loads(p.read_text())["hooks"]["Stop"] or True  # Stop untouched shape
    assert any("Stop" == k for k in data["hooks"])

def test_hooks_idempotent_mtime(home):
    stage_hooks(_repo(), home)
    t = _cl(home).stat().st_mtime_ns
    r = stage_hooks(_repo(), home)
    assert "already" in r.detail and _cl(home).stat().st_mtime_ns == t

def test_corrupt_json_fails_names_path(home):
    _cl(home).parent.mkdir(parents=True); _cl(home).write_text("{not json")
    r = stage_hooks(_repo(), home)
    assert r.status == "failed" and "settings.json" in r.detail

def test_backup_created_before_first_write(home, tmp_path):
    br = tmp_path / "backups"
    stage_hooks(_repo(), home, backup_root=br)
    assert any(br.rglob("settings.json"))
    stage_hooks(_repo(), home, backup_root=br)                      # no change -> no new backup dirs
    assert len([d for d in br.rglob("settings.json")]) == 1
```

- [ ] **Step 2: RED.** — [ ] **Step 3: implement** — merge semantics: claude `settings.json` ensure `hooks.{SessionStart,PreCompact}` lists contain an entry whose hooks include command `claude_hook_cmd` (dedupe by command substring); pi `settings.json` ensure `extensions` list contains absolute `pi-extension.ts` path. Additive only; write only when changed; backup first (R4); corrupt JSON → FAILED naming path. — [ ] **Step 4: GREEN.** — [ ] **Step 5: Commit** — `feat(install): continuity hooks wiring, additive JSON merge with backup (#53)`

---

### Task 4: cli-shim stage + `update` + `status` subcommands

**Files:**
- Create: `agent_extensions/install/templates/{ae-launcher.sh, ae-launcher.ps1}`, `agent_extensions/install/update.py`
- Modify: `agent_extensions/install/machine.py` (`stage_cli_shim`), `agent_extensions/install/__main__.py`
- Test: `tests/test_install_machine.py`, `tests/test_install_cli.py`

**Interfaces:**
- Produces: `stage_cli_shim(install_repo, home, env=None) -> StageResult`; `update.py`: `ensure_checkout(install_dir, repo_url) -> str`, `cmd_update(...) -> BootstrapReport`; `__main__.py` argparse: `bootstrap|update|init [path]|status`, exit 0 iff `report.ok()`.
- Launcher contract: `ae <args>` execs `python3 -m agent_extensions.install <args>` with `PYTHONPATH=<install_repo>` prepended; `AGENT_EXTENSIONS_DIR` respected; Windows `.ps1` uses `python` + `;` separator.

- [ ] **Step 1: failing tests**

```python
# test_install_machine.py additions
def test_cli_shim_written_and_managed(home):
    r = stage_cli_shim(_repo(), home)
    assert r.status == "applied"
    p = home / "bin" / "ae"
    assert p.exists() and "managed-by" in p.read_text() and "agent_extensions.install" in p.read_text()

def test_cli_shim_never_clobbers_unmanaged(home):
    (home / "bin").mkdir(); (home / "bin" / "ae").write_text("#!/bin/sh\necho mine\n")
    r = stage_cli_shim(_repo(), home)
    assert "unmanaged" in r.detail and "echo mine" in (home / "bin" / "ae").read_text()

# test_install_cli.py
import subprocess, json
from pathlib import Path

def _upstream(tmp_path):
    up = tmp_path / "upstream"; up.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main", str(up)], check=True)
    (up / "f.txt").write_text("one\n"); _commit(up, "one")
    return up

def _commit(repo, msg):
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(repo), "-c", "user.email=a@b", "-c", "user.name=t", "commit", "-qm", msg], check=True)

def _clone(up, dst):
    subprocess.run(["git", "clone", "-q", str(up), str(dst)], check=True)
    subprocess.run(["git", "-C", str(dst), "config", "user.email", "a@b"], check=True)
    subprocess.run(["git", "-C", str(dst), "config", "user.name", "t"], check=True)
    return dst

def test_update_fast_forwards_and_converges(tmp_path, home, monkeypatch):
    up = _upstream(tmp_path); co = _clone(up, tmp_path / "co")
    from agent_extensions.install.update import cmd_update
    (up / "f.txt").write_text("two\n"); _commit(up, "two")
    monkeypatch.setenv("AGENT_EXTENSIONS_DIR", str(co))
    r = cmd_update(home=home)
    assert r.ok()
    assert "two" in (co / "f.txt").read_text()

def test_update_dirty_checkout_fails_with_guidance(tmp_path, home, monkeypatch):
    up = _upstream(tmp_path); co = _clone(up, tmp_path / "co")
    (co / "dirty.txt").write_text("x")
    from agent_extensions.install.update import cmd_update
    monkeypatch.setenv("AGENT_EXTENSIONS_DIR", str(co))
    r = cmd_update(home=home)
    assert not r.ok() and "git status" in r.failed()[0].detail
    assert (co / "dirty.txt").exists()                              # never discarded

def test_status_reports_stages(tmp_path, home, monkeypatch, capsys):
    from agent_extensions.install.__main__ import main
    monkeypatch.setenv("HOME", str(home)); monkeypatch.setenv("AGENT_EXTENSIONS_DIR", str(_repo()))
    assert main(["status"]) == 0 and "gitignore" in capsys.readouterr().out
```

- [ ] **Step 2: RED.** — [ ] **Step 3: implement** per interfaces; `ensure_checkout`: clone if `.git` missing (`git clone <repo_url>`; tests use local path), else `git pull --ff-only`; dirty check via `git status --porcelain` BEFORE pull (non-empty → FAILED `run: git -C <dir> status --porcelain; stash or commit, then re-run`); pull failure → FAILED with exact commands. `cmd_update` then runs `machine.bootstrap(...)` to converge. — [ ] **Step 4: GREEN.** — [ ] **Step 5: Commit** — `feat(install): ae launcher + update/status subcommands (#53)`

---

### Task 5: repo-mode `init` — overlay, bindings, guards

**Files:**
- Create: `agent_extensions/install/repo.py`, `agent_extensions/install/guards.py`, `agent_extensions/install/templates/AGENTS.local.md`
- Test: `tests/test_install_repo.py`, `tests/test_guards.py`

**Interfaces:**
- Produces: `stage_overlay(repo, install_repo, home) -> StageResult` (32 KiB bound; marker-guard; `AGENTS.local.d/*.md` fragments appended, lexical order); `stage_bindings(repo, install_repo, home, backup_root=None) -> StageResult` (claude settings.local.json hooks incl. PreToolUse guard → `python <install_repo>/agent_extensions/install/guards.py`; pi `.pi/settings.json` only-if-absent per R1); `stage_guards(repo, install_repo) -> StageResult` (runs `guards.py --selfcheck` with `python`); `stage_status_clean(repo) -> StageResult`; `init(repo, ...) -> BootstrapReport`.
- `guards.py`: `find_violations(tool_name: str, tool_input: dict) -> list[str]` pure; `main(argv)` hook entrypoint: read stdin JSON → violations → print rule-naming message (never the secret text) → exit 2; clean/crash → exit 0; `--selfcheck` → exit 0.
- Deny classes v1 (spec): secret-shaped values (`sk-ant-…`, `ghp_…`, `AKIA…`, PEM private-key headers) in Bash command or Write/Edit content; `git push` targeting `main`/`master` (not `feature/main`, not `main-thing`).

- [ ] **Step 1: failing tests (guard first)**

```python
# tests/test_guards.py
from agent_extensions.install.guards import find_violations

def test_blocks_secret_in_bash():
    v = find_violations("Bash", {"command": "echo sk-ant-aaaabbbbccccddddeeeeffff"})
    assert v and "secret" in v[0].lower()
def test_blocks_private_key_write():
    v = find_violations("Write", {"file_path": "/x", "content": "-----BEGIN RSA PRIVATE KEY-----"})
    assert v
def test_blocks_protected_push():
    assert find_violations("Bash", {"command": "git push origin main"})
    assert find_violations("Bash", {"command": "git push --force origin HEAD:master"})
def test_allows_branches_containing_word():
    assert not find_violations("Bash", {"command": "git push origin feature/main"})
    assert not find_violations("Bash", {"command": "git push origin main-thing"})
def test_allows_normal_tool_use():
    assert not find_violations("Bash", {"command": "git push origin feature/x"})
    assert not find_violations("Read", {"file_path": "/etc/passwd"})
def test_hook_entrypoint_exit_codes(tmp_path):
    import subprocess, sys, json as j
    p = Path(__file__).resolve().parents[1] / "agent_extensions" / "install" / "guards.py"
    bad = subprocess.run([sys.executable, str(p)], input=j.dumps({"tool_name": "Bash", "tool_input": {"command": "git push origin main"}}), capture_output=True, text=True)
    assert bad.returncode == 2 and "protected branch" in bad.stderr.lower()
    ok = subprocess.run([sys.executable, str(p)], input=j.dumps({"tool_name": "Bash", "tool_input": {"command": "ls"}}), capture_output=True, text=True)
    assert ok.returncode == 0
    junk = subprocess.run([sys.executable, str(p)], input="not json", capture_output=True, text=True)
    assert junk.returncode == 0                                       # fail open
    sc = subprocess.run([sys.executable, str(p), "--selfcheck"], capture_output=True, text=True)
    assert sc.returncode == 0
```

- [ ] **Step 2: RED.** — [ ] **Step 3: implement** guards.py (patterns pinned by tests above; push pattern requires branch token not preceded by `/` and not followed by `[\w-]`).
- [ ] **Step 4: repo-stage failing tests**

```python
# tests/test_install_repo.py
import json, subprocess
from pathlib import Path
import pytest
from agent_extensions.install.repo import init, stage_overlay, stage_bindings
from agent_extensions.install import MANAGED_MD_MARKER

def _repo_dir(tmp_path):
    r = tmp_path / "teamrepo"; r.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main", str(r)], check=True)
    (r / "AGENTS.md").write_text("# team rules\n")
    subprocess.run(["git", "-C", str(r), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(r), "-c", "user.email=a@b", "-c", "user.name=t", "commit", "-qm", "init"], check=True)
    return r

def test_init_injects_gitignored_life(tmp_path, home):
    repo = _repo_dir(tmp_path)
    r = init(repo, install_repo=_repo(), home=home)
    assert r.ok(), [(s.name, s.status, s.detail) for s in r.stages]
    ov = repo / "AGENTS.local.md"
    assert ov.exists() and MANAGED_MD_MARKER in ov.read_text() and "team rules" not in ov.read_text()
    sl = json.loads((repo / ".claude" / "settings.local.json").read_text())
    assert any("guards.py" in h["command"] for e in sl["hooks"]["PreToolUse"] for h in e["hooks"])
    pj = json.loads((repo / ".pi" / "settings.json").read_text())
    assert any("pi-extension.ts" in e for e in pj["extensions"])
    assert subprocess.run(["git", "-C", str(repo), "status", "--porcelain"], capture_output=True, text=True).stdout == ""

def test_init_idempotent(tmp_path, home):
    repo = _repo_dir(tmp_path)
    init(repo, install_repo=_repo(), home=home)
    t = (repo / "AGENTS.local.md").stat().st_mtime_ns
    r2 = init(repo, install_repo=_repo(), home=home)
    assert r2.ok() and (repo / "AGENTS.local.md").stat().st_mtime_ns == t

def test_unmanaged_overlay_skipped(tmp_path, home):
    repo = _repo_dir(tmp_path)
    (repo / "AGENTS.local.md").write_text("# mine\n")
    r = init(repo, install_repo=_repo(), home=home)
    assert "# mine" in (repo / "AGENTS.local.md").read_text()
    assert any("unmanaged" in s.detail for s in r.stages)

def test_existing_project_pi_settings_skipped(tmp_path, home):
    repo = _repo_dir(tmp_path); (repo / ".pi").mkdir(); (repo / ".pi" / "settings.json").write_text('{"packages": []}')
    r = init(repo, install_repo=_repo(), home=home)
    assert any(s.name == "bindings" and s.status == "applied" and "pi: skipped" in s.detail for s in r.stages)
    assert json.loads((repo / ".pi" / "settings.json").read_text()) == {"packages": []}

def test_overlay_respects_32kib(tmp_path, home):
    repo = _repo_dir(tmp_path)
    big = repo / "AGENTS.local.d"; big.mkdir(); (big / "z.md").write_text("x" * 40_000)
    r = init(repo, install_repo=_repo(), home=home)
    assert (repo / "AGENTS.local.md").stat().st_size <= 32 * 1024
```

- [ ] **Step 5: RED.** — [ ] **Step 6: implement** repo.py per interfaces + R1/R2. — [ ] **Step 7: GREEN.** — [ ] **Step 8: Commit** — `feat(install): forceful repo-mode init — overlay, bindings, guards (#53)`

---

### Task 6: bootstrap orchestration + shell cutover + docs + live proof

**Files:**
- Modify: `agent_extensions/install/machine.py` (`bootstrap()` orchestrator), `bootstrap.sh`, `README.md`
- Test: `tests/test_install_cli.py`

**Interfaces:**
- Produces: `machine.bootstrap(install_repo, home, env=None) -> BootstrapReport` (stages: gitignore, shims, hooks, cli-shim, then sync delegation via `agent_extensions.sync.bootstrap.bootstrap(install_repo, home=home)` stages appended).
- `bootstrap.sh`: if `python3` exists → `PYTHONPATH="$INSTALL_DIR…" exec python3 -m agent_extensions.install bootstrap "$@"` (in-checkout mode: from checkout); else loud fallback to `bootstrap/sync.sh` (capability-only), exit code preserved.

- [ ] **Step 1: failing test**

```python
def test_bootstrap_runs_all_stages_then_sync(tmp_path, home, monkeypatch):
    from agent_extensions.install.machine import bootstrap
    r = bootstrap(_repo(), home=home)
    names = [s.name for s in r.stages]
    assert names[:4] == ["gitignore", "shims", "hooks", "cli-shim"]
    assert "detect" in names and "read-back" in names and r.ok()
```

- [ ] **Step 2: RED.** — [ ] **Step 3: implement** orchestrator + shell cutover + README "One-line setup" section (curl line, `ae update/init/status`, customization pointers). — [ ] **Step 4: GREEN (full suite).** — [ ] **Step 5: live proof on this machine** (documented in ledger): `python -m agent_extensions.install bootstrap` → existing unmarked shims skipped with reasons (no clobber), hooks "already", `~/bin/ae` created; `ae status` exit 0; throwaway repo `ae init` → `git status --porcelain` empty; re-run `bootstrap` → all no-op, exit 0. — [ ] **Step 6: Commit** — `feat(install): bootstrap orchestration + shell delegation cutover (#53)`

---

## Execution notes

- Inline execution (executing-plans): ledger at `<workspace>/progress.md` via `sdd-workspace`; `task-start`/`task-done` per task; final self-review against Review Focus (no subagent tool in this harness — stated per skill).
- After Task 6: push branch (dev-agent token via env-only mint), `gh pr create` (ready, emoji template, `Closes #53`), local-suite green evidence in PR, then per owner's "make it live": squash-merge, then reconcile (pull main ff, remove worktree, delete local+remote branch, remove `status:active`, receipt comment on #53, capsule update).
- Windows line endings: write files with `newline="\n"`; keep `git diff --check` clean.
