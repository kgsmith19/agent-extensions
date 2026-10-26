"""Claim guard as a Claude Code PreToolUse hook (#77; std#287 AC4).

The claim protocol's atomic step is creating the Issue's `issue/<n>-<slug>`
branch. The hook recognises that step in a PreToolUse payload, runs std's
`work_queue.py guard --claim` for the Issue, and blocks (exit 2, reason on
stderr) unless the Issue is on the active or next queue item. Everything
else passes immediately without touching the network. Tested black-box
against a stub std CLI served over file://, as in test_queue_cli.py.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from agent_extensions.install.machine import stage_hooks

ROOT = Path(__file__).resolve().parents[1]
HOOK = ROOT / "agent_extensions" / "queue_hook.py"
sys.path.insert(0, str(ROOT / "agent_extensions"))

STUB = r'''
import os, sys
open(os.environ["STUB_LOG"], "a").write(" ".join(sys.argv[1:]) + "\n")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import queue_core, queue_store
code = int(os.environ.get("STUB_GUARD_EXIT", "0"))
if code:
    sys.stderr.write("refused: guard: claim of %s refused, it is not on the active or next queue item\n" % sys.argv[-1])
else:
    print("guard: ok, %s is on Q32 (active)" % sys.argv[-1])
sys.exit(code)
'''


def served(tmp_path, modules=("queue_core.py", "queue_store.py", "work_queue.py")):
    d = tmp_path / "served"
    d.mkdir()
    for m in modules:
        (d / m).write_text(STUB if m == "work_queue.py" else "OK = True\n", encoding="utf-8")
    return d.as_uri()


def run_hook(payload, tmp_path, **env):
    full = {"PATH": os.environ["PATH"], "HOME": str(tmp_path), "STUB_LOG": str(tmp_path / "calls.log")}
    full.update(env)
    data = payload if isinstance(payload, str) else json.dumps(payload)
    result = subprocess.run([sys.executable, str(HOOK)], input=data, env=full,
                            capture_output=True, text=True, timeout=60)
    log = tmp_path / "calls.log"
    return result, (log.read_text().splitlines() if log.exists() else [])


CREATE_BRANCH = {"tool_name": "mcp__github__create_branch",
                 "tool_input": {"owner": "kgsmith19", "repo": "hyperbolic-core",
                                "branch": "issue/426-review-semantics", "from_branch": "main"}}


def test_claim_ref_recognises_both_atomic_claim_routes():
    """AC1: both ways the protocol creates the claim branch are recognised; nothing else is."""
    import queue_hook
    assert queue_hook.claim_ref(CREATE_BRANCH) == "kgsmith19/hyperbolic-core#426"
    gh = {"tool_name": "Bash", "tool_input": {"command":
          "gh api repos/kgsmith19/agent-extensions/git/refs -X POST -f ref=refs/heads/issue/77-x -f sha=abc"}}
    assert queue_hook.claim_ref(gh) == "kgsmith19/agent-extensions#77"
    for payload in (
        {"tool_name": "mcp__github__create_branch",
         "tool_input": {"owner": "o", "repo": "r", "branch": "claude/feature"}},
        {"tool_name": "Bash", "tool_input": {"command": "git status"}},
        {"tool_name": "Bash", "tool_input": {"command": "gh api repos/o/r/git/refs/heads/issue/5-x"}},
        {"tool_name": "Read", "tool_input": {"file_path": "/x"}},
        {},
    ):
        assert queue_hook.claim_ref(payload) is None, payload


def test_allowed_claim_passes(tmp_path):
    """AC1: a claim on the active or next item proceeds (exit 0)."""
    result, calls = run_hook(CREATE_BRANCH, tmp_path, AE_QUEUE_BASE_URL=served(tmp_path))
    assert result.returncode == 0, result.stderr
    assert calls == ["guard --claim kgsmith19/hyperbolic-core#426"]


def test_refused_claim_is_blocked_with_the_guards_reason(tmp_path):
    """AC1: out-of-order work is stopped before the branch exists, and the agent is told why."""
    result, calls = run_hook(CREATE_BRANCH, tmp_path, AE_QUEUE_BASE_URL=served(tmp_path),
                             STUB_GUARD_EXIT="1")
    assert result.returncode == 2
    assert "kgsmith19/hyperbolic-core#426" in result.stderr
    assert "not on the active or next queue item" in result.stderr


def test_unreadable_queue_fails_closed(tmp_path):
    """AC2: if the queue cannot be read, the claim is blocked, never silently allowed."""
    result, calls = run_hook(CREATE_BRANCH, tmp_path,
                             AE_QUEUE_BASE_URL=(tmp_path / "nowhere").as_uri())
    assert result.returncode == 2
    assert "queue" in result.stderr.lower()
    result, _ = run_hook(CREATE_BRANCH, tmp_path, AE_QUEUE_BASE_URL=served(tmp_path),
                         STUB_GUARD_EXIT="5")
    assert result.returncode == 2


def test_non_claims_pass_without_any_network_call(tmp_path):
    """AC1: every other tool call is untouched and costs no fetch."""
    for payload in ({"tool_name": "Bash", "tool_input": {"command": "ls"}},
                    {"tool_name": "Edit", "tool_input": {}}, "not json at all"):
        result, calls = run_hook(payload, tmp_path,
                                 AE_QUEUE_BASE_URL=(tmp_path / "nowhere").as_uri())
        assert result.returncode == 0, (payload, result.stderr)
        assert calls == []


def _pre_tool_use(home):
    data = json.loads((home / ".claude" / "settings.json").read_text(encoding="utf-8"))
    return data["hooks"]["PreToolUse"]


def _ours(entries):
    return [e for e in entries if any("queue_hook.py" in h.get("command", "") for h in e.get("hooks", []))]


def test_stage_hooks_registers_one_pre_tool_use_entry(tmp_path):
    """AC3: bootstrap wires the hook for every repo on the machine, with the claim matcher."""
    home = tmp_path / "home"
    stage_hooks(ROOT, home)
    ours = _ours(_pre_tool_use(home))
    assert len(ours) == 1
    assert ours[0]["matcher"] == "mcp__github__create_branch|Bash"
    assert ours[0]["hooks"] == [{"type": "command",
                                 "command": 'python "' + (ROOT / "agent_extensions" / "queue_hook.py").as_posix() + '"'}]


def test_stage_hooks_is_additive_idempotent_and_converges(tmp_path):
    """AC3: foreign PreToolUse hooks survive, a re-run changes nothing, a stale path converges to one entry."""
    home = tmp_path / "home"
    settings = home / ".claude" / "settings.json"
    settings.parent.mkdir(parents=True)
    foreign = {"matcher": "Write", "hooks": [{"type": "command", "command": "echo mine"}]}
    stale = {"matcher": "Bash", "hooks": [{"type": "command",
                                            "command": 'python "/old/agent_extensions/queue_hook.py"'}]}
    settings.write_text(json.dumps({"hooks": {"PreToolUse": [foreign, stale]}}), encoding="utf-8")
    stage_hooks(ROOT, home)
    entries = _pre_tool_use(home)
    assert foreign in entries
    assert len(_ours(entries)) == 1
    assert "/old/" not in json.dumps(_ours(entries))
    before = settings.read_text(encoding="utf-8")
    report = stage_hooks(ROOT, home)
    assert settings.read_text(encoding="utf-8") == before
    assert "already current" in report.detail
