"""Machine-level install stages (issue #53): gitignore, shims, hooks, cli-shim."""
import json
import os
from pathlib import Path

import pytest

from agent_extensions.install import GITIGNORE_ENTRIES, MANAGED_MD_MARKER
from agent_extensions.install.machine import stage_gitignore


@pytest.fixture()
def home(tmp_path, monkeypatch):
    h = tmp_path / "home"
    h.mkdir()
    monkeypatch.setenv("HOME", str(h))
    monkeypatch.setenv("USERPROFILE", str(h))
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(h / ".gitconfig"))
    return h


def _repo() -> Path:
    return Path(__file__).resolve().parents[1]


def test_sets_excludesfile_and_appends_entries(home):
    r = stage_gitignore(home)
    assert r.status == "applied"
    excl = home / ".config" / "git" / "ignore"
    assert excl.exists()
    lines = excl.read_text(encoding="utf-8").splitlines()
    for e in GITIGNORE_ENTRIES:
        assert e in lines
    out = os.popen("git config --global core.excludesFile").read().strip()
    assert Path(out).resolve() == excl.resolve()


def test_idempotent_no_new_lines(home):
    stage_gitignore(home)
    before = (home / ".config" / "git" / "ignore").read_text(encoding="utf-8")
    r = stage_gitignore(home)
    assert r.status == "applied" and "up to date" in r.detail
    assert (home / ".config" / "git" / "ignore").read_text(encoding="utf-8") == before


def test_never_removes_user_lines(home):
    excl = home / ".config" / "git" / "ignore"
    excl.parent.mkdir(parents=True)
    excl.write_text("*.log\nnode_modules/\n", encoding="utf-8")
    stage_gitignore(home)
    txt = excl.read_text(encoding="utf-8")
    assert "*.log\n" in txt and "node_modules/\n" in txt


def test_missing_git_is_skipped_not_failed(home, monkeypatch):
    monkeypatch.setenv("PATH", "")
    r = stage_gitignore(home)
    assert r.status == "skipped"


# ---- Task 2: shims ----
from agent_extensions.install.machine import stage_shims  # noqa: E402


def _shim_gates(home: Path) -> dict[str, Path]:
    gates = {}
    for d in (".pi/agent", ".claude", ".codex", ".gemini", ".config/kilo"):
        p = home / d
        p.mkdir(parents=True, exist_ok=True)
        gates[d] = p
    return gates


def test_fresh_home_writes_ungated_baseline_and_gated_shims(home):
    _shim_gates(home)
    r = stage_shims(_repo(), home)
    assert r.status == "applied"
    baseline = (home / "AGENTS.md").read_text(encoding="utf-8")
    assert baseline.count(MANAGED_MD_MARKER) == 1
    assert "Global Agent Rules" in baseline
    assert "~/.config/agents" in baseline and "C:\\Users" not in baseline
    pi_shim = (home / ".pi" / "agent" / "AGENTS.md").read_text(encoding="utf-8")
    assert "pi Global Instructions" in pi_shim and MANAGED_MD_MARKER in pi_shim
    assert "C:\\Users" not in (home / ".claude" / "CLAUDE.md").read_text(encoding="utf-8")


def test_missing_gate_dir_is_skipped_with_reason(home):
    r = stage_shims(_repo(), home)
    assert r.status == "applied"
    assert not (home / ".codex").exists()
    assert "codex: skipped" in r.detail
    assert "kilo: skipped" in r.detail


def test_unmanaged_shim_never_clobbered(home):
    _shim_gates(home)
    target = home / ".claude" / "CLAUDE.md"
    target.write_text("# my hand-written rules\nKEEP ME\n", encoding="utf-8")
    r = stage_shims(_repo(), home)
    assert "KEEP ME" in target.read_text(encoding="utf-8")
    assert "unmanaged" in r.detail


def test_unmanaged_baseline_never_clobbered(home):
    _shim_gates(home)
    (home / "AGENTS.md").write_text("# mine\n", encoding="utf-8")
    r = stage_shims(_repo(), home)
    assert "# mine" in (home / "AGENTS.md").read_text(encoding="utf-8")
    assert "unmanaged" in r.detail


def test_managed_shim_updated_on_template_change(home):
    _shim_gates(home)
    (home / "AGENTS.md").write_text(MANAGED_MD_MARKER + "\nstale\n", encoding="utf-8")
    stage_shims(_repo(), home)
    assert "ZZSTALETOKENZZ" not in (home / "AGENTS.md").read_text(encoding="utf-8")


def test_shim_idempotent_mtime(home):
    _shim_gates(home)
    stage_shims(_repo(), home)
    t = (home / "AGENTS.md").stat().st_mtime_ns
    r = stage_shims(_repo(), home)
    assert "up to date" in r.detail
    assert (home / "AGENTS.md").stat().st_mtime_ns == t


# ---- Task 3: hooks ----
from agent_extensions.install.machine import stage_hooks


def test_hooks_merge_preserves_foreign_keys(home):
    p = home / ".claude" / "settings.json"
    p.parent.mkdir(parents=True)
    p.write_text(json.dumps({"model": "opus", "hooks": {"Stop": [{"hooks": [{"type": "command", "command": "echo mine"}]}]}}), encoding="utf-8")
    r = stage_hooks(_repo(), home)
    assert r.status == "applied"
    data = json.loads(p.read_text(encoding="utf-8"))
    assert data["model"] == "opus"
    assert data["hooks"]["Stop"] == [{"hooks": [{"type": "command", "command": "echo mine"}]}]
    assert any("claude_hook.py" in h["command"] for e in data["hooks"]["SessionStart"] for h in e["hooks"])
    assert any("claude_hook.py" in h["command"] for e in data["hooks"]["PreCompact"] for h in e["hooks"])


def test_hooks_creates_missing_settings(home):
    r = stage_hooks(_repo(), home)
    assert r.status == "applied"
    pi = json.loads((home / ".pi" / "agent" / "settings.json").read_text(encoding="utf-8"))
    assert any("pi-extension.ts" in e for e in pi["extensions"])


def test_hooks_idempotent_mtime(home):
    stage_hooks(_repo(), home)
    t = (home / ".claude" / "settings.json").stat().st_mtime_ns
    r = stage_hooks(_repo(), home)
    assert "already" in r.detail
    assert (home / ".claude" / "settings.json").stat().st_mtime_ns == t


def test_hooks_corrupt_json_fails_naming_path(home):
    p = home / ".claude" / "settings.json"
    p.parent.mkdir(parents=True)
    p.write_text("{not json", encoding="utf-8")
    r = stage_hooks(_repo(), home)
    assert r.status == "failed" and "settings.json" in r.detail


def test_hooks_backup_before_first_write(home, tmp_path):
    br = tmp_path / "backups"
    p = home / ".claude" / "settings.json"
    p.parent.mkdir(parents=True)
    p.write_text("{}", encoding="utf-8")
    stage_hooks(_repo(), home, backup_root=br)
    assert len(list(br.rglob("settings.json"))) == 1
    stage_hooks(_repo(), home, backup_root=br)  # no change -> no new backup
    assert len(list(br.rglob("settings.json"))) == 1


# ---- Task 4: cli-shim ----
from agent_extensions.install.machine import stage_cli_shim


def test_cli_shim_written_and_managed(home):
    r = stage_cli_shim(_repo(), home)
    assert r.status == "applied"
    p = home / "bin" / "ae"
    assert p.exists()
    txt = p.read_text(encoding="utf-8")
    assert "managed-by: agent-extensions" in txt
    assert "agent_extensions.install" in txt


def test_cli_shim_never_clobbers_unmanaged(home):
    (home / "bin").mkdir(parents=True)
    (home / "bin" / "ae").write_text("#!/bin/sh\necho mine\n", encoding="utf-8")
    r = stage_cli_shim(_repo(), home)
    assert "unmanaged" in r.detail
    assert "echo mine" in (home / "bin" / "ae").read_text(encoding="utf-8")


def test_cli_shim_idempotent_mtime(home):
    stage_cli_shim(_repo(), home)
    t = (home / "bin" / "ae").stat().st_mtime_ns
    r = stage_cli_shim(_repo(), home)
    assert "up to date" in r.detail
    assert (home / "bin" / "ae").stat().st_mtime_ns == t


def test_hooks_converge_stale_our_entries(home):
    """Our entries converge to one at the current install path (self-healing)."""
    p = home / ".claude" / "settings.json"
    p.parent.mkdir(parents=True)
    stale = "python \"C:/somewhere-else/agent_extensions/continuity/adapters/claude_hook.py\""
    p.write_text(json.dumps({"hooks": {"SessionStart": [
        {"hooks": [{"type": "command", "command": stale}]},
        {"hooks": [{"type": "command", "command": stale}]},  # duplicate
    ]}}), encoding="utf-8")
    r = stage_hooks(_repo(), home)
    assert r.status == "applied"
    data = json.loads(p.read_text(encoding="utf-8"))
    entries = data["hooks"]["SessionStart"]
    ours = [e for e in entries if any("claude_hook.py" in h.get("command", "") for h in e.get("hooks", []))]
    assert len(ours) == 1
    assert Path(_repo()).as_posix() in ours[0]["hooks"][0]["command"]


def test_cli_shim_respects_env_override(home):
    """Rendered launcher defaults to the install path but lets AGENT_EXTENSIONS_DIR win."""
    r = stage_cli_shim(_repo(), home)
    assert r.status == "applied"
    txt = (home / "bin" / "ae").read_text(encoding="utf-8")
    assert '${AGENT_EXTENSIONS_DIR:-' in txt
