"""Repo-mode init contract (issue #53): forceful, gitignored, idempotent."""

import json
import subprocess
from pathlib import Path

import pytest

from agent_extensions.install import MANAGED_MD_MARKER
from agent_extensions.install.repo import init


def _repo() -> Path:
    return Path(__file__).resolve().parents[1]


@pytest.fixture()
def home(tmp_path, monkeypatch):
    h = tmp_path / "home"
    h.mkdir()
    monkeypatch.setenv("HOME", str(h))
    monkeypatch.setenv("USERPROFILE", str(h))
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(h / ".gitconfig"))
    return h


@pytest.fixture()
def teamrepo(tmp_path, home):
    """A fresh repo owned by a team: committed AGENTS.md, no local overlays."""
    r = tmp_path / "teamrepo"
    r.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main", str(r)], check=True)
    (r / "AGENTS.md").write_text("# team rules\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(r), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(r), "-c", "user.email=a@b", "-c", "user.name=t", "commit", "-qm", "init"], check=True)
    return r


def test_init_injects_gitignored_life(teamrepo, home):
    r = init(teamrepo, install_repo=_repo(), home=home)
    assert r.ok(), [(s.name, s.status, s.detail) for s in r.stages]
    ov = teamrepo / "AGENTS.local.md"
    assert ov.exists() and MANAGED_MD_MARKER in ov.read_text(encoding="utf-8")
    assert "team rules" not in ov.read_text(encoding="utf-8")  # additive, never merges team file
    sl = json.loads((teamrepo / ".claude" / "settings.local.json").read_text(encoding="utf-8"))
    assert any("guards.py" in h["command"] for e in sl["hooks"]["PreToolUse"] for h in e["hooks"])
    assert any("claude_hook.py" in h["command"] for e in sl["hooks"]["SessionStart"] for h in e["hooks"])
    pj = json.loads((teamrepo / ".pi" / "settings.json").read_text(encoding="utf-8"))
    assert any("pi-extension.ts" in e for e in pj["extensions"])
    assert subprocess.run(["git", "-C", str(teamrepo), "status", "--porcelain"], capture_output=True, text=True).stdout == ""


def test_init_idempotent_mtime(teamrepo, home):
    init(teamrepo, install_repo=_repo(), home=home)
    t = (teamrepo / "AGENTS.local.md").stat().st_mtime_ns
    r2 = init(teamrepo, install_repo=_repo(), home=home)
    assert r2.ok()
    assert (teamrepo / "AGENTS.local.md").stat().st_mtime_ns == t


def test_unmanaged_overlay_skipped(teamrepo, home):
    (teamrepo / "AGENTS.local.md").write_text("# mine\n", encoding="utf-8")
    r = init(teamrepo, install_repo=_repo(), home=home)
    assert "# mine" in (teamrepo / "AGENTS.local.md").read_text(encoding="utf-8")
    assert any("unmanaged" in s.detail for s in r.stages)


def test_existing_project_pi_settings_skipped(teamrepo, home):
    (teamrepo / ".pi").mkdir()
    (teamrepo / ".pi" / "settings.json").write_text('{"packages": []}', encoding="utf-8")
    r = init(teamrepo, install_repo=_repo(), home=home)
    bindings = next(s for s in r.stages if s.name == "bindings")
    assert bindings.status == "applied" and "pi: skipped" in bindings.detail
    assert json.loads((teamrepo / ".pi" / "settings.json").read_text(encoding="utf-8")) == {"packages": []}


def test_overlay_bounded_32kib(teamrepo, home):
    d = teamrepo / "AGENTS.local.d"
    d.mkdir()
    (d / "z.md").write_text("x" * 40_000, encoding="utf-8")
    r = init(teamrepo, install_repo=_repo(), home=home)
    overlay = next(s for s in r.stages if s.name == "overlay")
    assert overlay.status == "failed" and "exceeds" in overlay.detail  # loud, never oversize
    assert not (teamrepo / "AGENTS.local.md").exists()
