"""Guard contract (issue #53): deny classes pinned by tests; fail-open crash."""

import json
import subprocess
import sys
from pathlib import Path

from agent_extensions.install.guards import find_violations


def _repo() -> Path:
    return Path(__file__).resolve().parents[1]


def _guard() -> Path:
    return _repo() / "agent_extensions" / "install" / "guards.py"


def test_blocks_secret_in_bash():
    v = find_violations("Bash", {"command": "echo sk-ant-" + "a" * 24})
    assert v and "secret" in v[0].lower()


def test_blocks_github_token_and_aws_key_shapes():
    assert find_violations("Bash", {"command": "echo ghp_" + "a" * 24})
    assert find_violations("Bash", {"command": "echo AKIA" + "A" * 16})


def test_blocks_private_key_write():
    v = find_violations("Write", {"file_path": "/x", "content": "-----BEGIN RSA PRIVATE KEY-----"})
    assert v and "secret" in v[0].lower()


def test_blocks_protected_push():
    assert find_violations("Bash", {"command": "git push origin main"})
    assert find_violations("Bash", {"command": "git push --force origin HEAD:master"})
    assert find_violations("Bash", {"command": "git push upstream main"})


def test_allows_branches_containing_protected_words():
    assert not find_violations("Bash", {"command": "git push origin feature/main"})
    assert not find_violations("Bash", {"command": "git push origin main-thing"})
    assert not find_violations("Bash", {"command": "git push origin feature/x"})


def test_allows_normal_tool_use():
    assert not find_violations("Read", {"file_path": "/etc/passwd"})
    assert not find_violations("Bash", {"command": "echo hello world"})


def test_guard_hook_entrypoint():
    block = subprocess.run(
        [sys.executable, str(_guard())],
        input=json.dumps({"tool_name": "Bash", "tool_input": {"command": "git push origin main"}}),
        capture_output=True, text=True,
    )
    assert block.returncode == 2
    assert "protected branch" in block.stderr.lower()
    ok = subprocess.run(
        [sys.executable, str(_guard())],
        input=json.dumps({"tool_name": "Bash", "tool_input": {"command": "git status"}}),
        capture_output=True, text=True,
    )
    assert ok.returncode == 0
    junk = subprocess.run([sys.executable, str(_guard())], input="not json", capture_output=True, text=True)
    assert junk.returncode == 0  # fail open
    selfcheck = subprocess.run([sys.executable, str(_guard()), "--selfcheck"], capture_output=True, text=True)
    assert selfcheck.returncode == 0
