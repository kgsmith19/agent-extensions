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
