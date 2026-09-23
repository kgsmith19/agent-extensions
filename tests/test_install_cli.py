"""CLI contract for agent_extensions.install (issue #53): bootstrap/update/init/status."""
import subprocess
from pathlib import Path

import pytest

from agent_extensions.install.update import cmd_update


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


def _commit(repo: Path, msg: str) -> None:
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(repo), "-c", "user.email=a@b", "-c", "user.name=t", "commit", "-qm", msg], check=True)


def _upstream(tmp_path: Path) -> Path:
    up = tmp_path / "upstream"
    subprocess.run(["git", "clone", "-q", str(_repo()), str(up)], check=True)
    subprocess.run(["git", "-C", str(up), "config", "user.email", "a@b"], check=True)
    subprocess.run(["git", "-C", str(up), "config", "user.name", "t"], check=True)
    return up


def _clone(up: Path, dst: Path) -> Path:
    subprocess.run(["git", "clone", "-q", str(up), str(dst)], check=True)
    subprocess.run(["git", "-C", str(dst), "config", "user.email", "a@b"], check=True)
    subprocess.run(["git", "-C", str(dst), "config", "user.name", "t"], check=True)
    return dst


def test_update_fast_forwards_and_converges(tmp_path, home, monkeypatch):
    up = _upstream(tmp_path)
    co = _clone(up, tmp_path / "co")
    (up / "f.txt").write_text("two\n", encoding="utf-8")
    _commit(up, "two")
    monkeypatch.setenv("AGENT_EXTENSIONS_DIR", str(co))
    r = cmd_update(home=home)
    assert r.ok(), [(s.name, s.status, s.detail) for s in r.stages]
    assert "two" in (co / "f.txt").read_text(encoding="utf-8")
    names = [s.name for s in r.stages]
    assert "gitignore" in names


def test_update_dirty_checkout_fails_with_guidance_never_discards(tmp_path, home, monkeypatch):
    up = _upstream(tmp_path)
    co = _clone(up, tmp_path / "co")
    (co / "dirty.txt").write_text("precious\n", encoding="utf-8")
    monkeypatch.setenv("AGENT_EXTENSIONS_DIR", str(co))
    r = cmd_update(home=home)
    assert not r.ok()
    assert "status --porcelain" in r.failed()[0].detail
    assert (co / "dirty.txt").read_text(encoding="utf-8") == "precious\n"


def test_status_reports_stage_names(tmp_path, home, monkeypatch, capsys):
    from agent_extensions.install.__main__ import main
    monkeypatch.setenv("AGENT_EXTENSIONS_DIR", str(_repo()))
    from agent_extensions.install.__main__ import main
    rc = main(["status"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "gitignore" in out and "shims" in out
