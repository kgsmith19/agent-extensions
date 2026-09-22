import json
import subprocess
from pathlib import Path

from agent_extensions.continuity import capsule as C
from agent_extensions.continuity.__main__ import main


def _git_repo(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=path, check=True)
    (path / "a.txt").write_text("hi")
    subprocess.run(["git", "add", "."], cwd=path, check=True)
    subprocess.run(["git", "commit", "-qm", "init"], cwd=path, check=True)
    return path


def test_project_key_and_path(tmp_path):
    store = tmp_path / "store"
    repo = _git_repo(tmp_path / "myproj")
    assert C.project_key(repo) == "myproj"
    assert C.capsule_path(repo, store).name == "myproj.json"


def test_capture_and_render_roundtrip(tmp_path):
    store = tmp_path / "store"
    repo = _git_repo(tmp_path / "myproj")
    rc = main(
        [
            "capture", "--cwd", str(repo), "--dir", str(store),
            "--task", "T", "--next", "N", "--phase", "IMPLEMENT",
            "--blocker", "B1", "--issue", "44", "--harness", "pi", "--model", "m",
        ]
    )
    assert rc == 0
    cap = C.Capsule.load(C.capsule_path(repo, store))
    assert (cap.task, cap.next_action, cap.phase) == ("T", "N", "IMPLEMENT")
    assert cap.blockers == ["B1"] and cap.open_issues == [44]
    assert cap.branch and cap.head and cap.repo
    text = cap.render("plain")
    assert "Continuity capsule" in text and "next action: N" in text
    claude = json.loads(cap.render("claude"))
    assert claude["hookSpecificOutput"]["hookEventName"] == "SessionStart"
    assert "next action: N" in claude["hookSpecificOutput"]["additionalContext"]


def test_auto_refresh_preserves_manual_fields(tmp_path):
    store = tmp_path / "store"
    repo = _git_repo(tmp_path / "myproj")
    main(["capture", "--cwd", str(repo), "--dir", str(store), "--task", "T", "--next", "N"])
    main(["capture", "--cwd", str(repo), "--dir", str(store), "--auto"])
    cap = C.Capsule.load(C.capsule_path(repo, store))
    assert (cap.task, cap.next_action) == ("T", "N")


def test_render_empty_is_blank(tmp_path, capsys):
    store = tmp_path / "store"
    plain = tmp_path / "plain"
    plain.mkdir()
    assert main(["render", "--cwd", str(plain), "--dir", str(store)]) == 0
    assert capsys.readouterr().out == ""


def test_clear(tmp_path):
    store = tmp_path / "store"
    repo = _git_repo(tmp_path / "myproj")
    main(["capture", "--cwd", str(repo), "--dir", str(store), "--task", "T"])
    path = C.capsule_path(repo, store)
    assert path.exists()
    main(["clear", "--cwd", str(repo), "--dir", str(store)])
    assert not path.exists()


def test_harness_detection_is_provider_neutral(monkeypatch):
    for env in ("PI_CODING_AGENT", "CLAUDECODE", "CLAUDE_CODE", "CODEX_SANDBOX", "ANTIGRAVITY"):
        monkeypatch.delenv(env, raising=False)
    assert C.detect_harness() == ""
    monkeypatch.setenv("CLAUDECODE", "1")
    assert C.detect_harness() == "claude"
    monkeypatch.delenv("CLAUDECODE")
    monkeypatch.setenv("PI_CODING_AGENT", "1")
    assert C.detect_harness() == "pi"


def test_read_local_overlay_absent_is_empty(tmp_path):
    assert C.read_local_overlay(tmp_path) == ""


def test_local_overlay_and_capsule_both_injected(tmp_path, capsys):
    """A gitignored AGENTS.local.md rides in the same session-start injection."""
    store = tmp_path / "store"
    repo = _git_repo(tmp_path / "myproj")
    (repo / C.LOCAL_OVERLAY_NAME).write_text(
        "# My local process\n- always run the focused test first\n", encoding="utf-8"
    )
    main(["capture", "--cwd", str(repo), "--dir", str(store), "--task", "T", "--next", "N"])
    assert main(["render", "--cwd", str(repo), "--dir", str(store)]) == 0
    out = capsys.readouterr().out
    assert "My local process" in out and "next action: N" in out
    assert out.index("My local process") < out.index("Continuity capsule")


def test_local_overlay_survives_empty_capsule(tmp_path, capsys):
    store = tmp_path / "store"
    repo = tmp_path / "plain"
    repo.mkdir()
    (repo / C.LOCAL_OVERLAY_NAME).write_text("local only\n", encoding="utf-8")
    assert main(["render", "--cwd", str(repo), "--dir", str(store)]) == 0
    assert "local only" in capsys.readouterr().out


def test_local_overlay_claude_format_wraps_both(tmp_path, capsys):
    store = tmp_path / "store"
    repo = _git_repo(tmp_path / "myproj")
    (repo / C.LOCAL_OVERLAY_NAME).write_text("overlay rule\n", encoding="utf-8")
    main(["capture", "--cwd", str(repo), "--dir", str(store), "--task", "T"])
    capsys.readouterr()  # discard the capture path line
    assert main(["render", "--format", "claude", "--cwd", str(repo), "--dir", str(store)]) == 0
    ctx = json.loads(capsys.readouterr().out)["hookSpecificOutput"]["additionalContext"]
    assert "overlay rule" in ctx and "task: T" in ctx


def test_local_overlay_is_bounded(tmp_path):
    repo = tmp_path / "big"
    repo.mkdir()
    (repo / C.LOCAL_OVERLAY_NAME).write_text(
        "x" * (C.LOCAL_OVERLAY_MAX_BYTES + 500), encoding="utf-8"
    )
    assert len(C.read_local_overlay(repo)) <= C.LOCAL_OVERLAY_MAX_BYTES
