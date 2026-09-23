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
