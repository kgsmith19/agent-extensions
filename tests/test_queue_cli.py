"""agent_extensions/queue.py (#72, Queue v2 phase 3 / std#286): brief + forwarding.

Black-box through subprocesses, against a stub of std's queue CLI with the
same layout (work_queue.py importing its siblings from its own directory).
The stub dispatches on argv, records every call in a log, and takes its
exit codes from env, so each test can assert output, exit code and exactly
which calls were made.
"""
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
QUEUE = ROOT / "agent_extensions" / "queue.py"

STUB = r'''
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import queue_core, queue_store
log = os.environ.get("STUB_LOG")
if log:
    with open(log, "a") as handle:
        handle.write(" ".join(sys.argv[1:]) + "\n")
args = sys.argv[1:]
code = int(os.environ.get("STUB_EXIT_" + "_".join(args).upper(), "0"))
if code not in (0, 3):
    sys.exit("stub failure for " + " ".join(args))
if args == ["next"]:
    print("QUEUE EMPTY" if code == 3 else "Q99 [active] stub head — o/r#1\n    stub note")
elif args == ["show"]:
    print("Q02 [blocked] gated — o/r#2\nQ99 [active] stub head — o/r#1\n    stub note\nQ100 [pending] later — no refs")
elif args == ["handoff", "show"]:
    print("NO HANDOFF" if code == 3 else "## Handoff\nstep one")
else:
    print("forwarded: " + " ".join(args))
    sys.stderr.write("stderr passthrough\n")
sys.exit(code)
'''


def make_stub(directory: Path, modules=("queue_core", "queue_store")) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "work_queue.py").write_text(STUB, encoding="utf-8")
    for module in modules:
        (directory / (module + ".py")).write_text("OK = True\n", encoding="utf-8")
    return directory


def run(args, env_extra, tmp_path):
    env = {"PATH": os.environ["PATH"], "HOME": str(tmp_path / "home"),
           "STUB_LOG": str(tmp_path / "calls.log")}
    env.update(env_extra)
    return subprocess.run([sys.executable, "-I", str(QUEUE), *args], env=env,
                          capture_output=True, text=True, timeout=60)


def calls(tmp_path):
    log = tmp_path / "calls.log"
    return log.read_text().splitlines() if log.exists() else []


@pytest.fixture()
def fetched(tmp_path):
    """Env pointing the fetch path at a full stub served over file://."""
    stub = make_stub(tmp_path / "served")
    return {"AE_QUEUE_BASE_URL": stub.as_uri()}


def test_brief_prints_active_next_and_handoff_in_order(tmp_path, fetched):
    """AC1: a fresh session sees where it is: the active item, what is next, and how to work it."""
    result = run(["brief"], fetched, tmp_path)
    assert result.returncode == 0, result.stderr
    out = result.stdout
    for label in ("Active item:", "Next item:", "Handoff:"):
        assert label in out
    assert out.index("Active item:") < out.index("Next item:") < out.index("Handoff:")
    active = out.split("Active item:")[1].split("Next item:")[0]
    assert "Q99 [active] stub head" in active and "stub note" in active
    assert "Q02" not in active and "Q100" not in active
    assert "## Handoff\nstep one" in out.split("Handoff:")[1]
    assert calls(tmp_path) == ["next", "show", "handoff show"]


def test_local_standard_checkout_is_preferred(tmp_path):
    """AC1: a local std checkout is used as is, so sessions in std run the code under review."""
    std = tmp_path / "std"
    make_stub(std / "tools")
    result = run(["brief"], {"AES_STANDARD_DIR": str(std),
                             "AE_QUEUE_BASE_URL": (tmp_path / "nowhere").as_uri()}, tmp_path)
    assert result.returncode == 0, result.stderr
    assert "Q99 [active] stub head" in result.stdout


def test_missing_module_degrades_without_running_the_cli(tmp_path):
    """AC1: a half-fetched CLI is never run; the failure names the module that was missing."""
    stub = make_stub(tmp_path / "partial", modules=("queue_core",))
    result = run(["brief"], {"AE_QUEUE_BASE_URL": stub.as_uri()}, tmp_path)
    assert result.returncode != 0
    last = (result.stdout + result.stderr).strip().splitlines()[-1]
    assert "queue_store.py" in last
    assert calls(tmp_path) == []


def test_failing_next_fails_brief_with_its_message(tmp_path, fetched):
    """AC1/AC2: if the queue itself is unreadable the session must say so, with the CLI's own reason last."""
    result = run(["brief"], dict(fetched, STUB_EXIT_NEXT="2"), tmp_path)
    assert result.returncode == 2
    assert (result.stdout + result.stderr).strip().splitlines()[-1] == "stub failure for next"


def test_failing_or_empty_sections_degrade_individually(tmp_path, fetched):
    """AC1: a missing handoff or a failed show must not hide the next item, and must be visible."""
    result = run(["brief"], dict(fetched, STUB_EXIT_SHOW="5", STUB_EXIT_HANDOFF_SHOW="3"), tmp_path)
    assert result.returncode == 0
    assert "Active item: degraded (exit 5: stub failure for show)" in result.stdout
    assert "Q99 [active] stub head" in result.stdout.split("Next item:")[1]
    assert "Handoff: none" in result.stdout
    result = run(["brief"], dict(fetched, STUB_EXIT_HANDOFF_SHOW="1"), tmp_path)
    assert "Handoff: degraded (exit 1: stub failure for handoff show)" in result.stdout


def test_empty_queue_brief_exits_3(tmp_path, fetched):
    """AC1: an empty queue keeps std's exit 3 so the session start reports it as a normal state."""
    result = run(["brief"], dict(fetched, STUB_EXIT_NEXT="3"), tmp_path)
    assert result.returncode == 3
    assert "QUEUE EMPTY" in result.stdout


def test_forwarding_preserves_args_output_and_exit_code(tmp_path, fetched):
    """AC3: `ae queue` is std's CLI: same arguments, same streams, same exit code."""
    result = run(["history", "Q32"], dict(fetched, STUB_EXIT_HISTORY_Q32="3"), tmp_path)
    assert result.returncode == 3
    assert result.stdout.strip() == "forwarded: history Q32"
    assert result.stderr.strip() == "stderr passthrough"
    result = run(["next"], dict(fetched, STUB_EXIT_NEXT="3"), tmp_path)
    assert (result.returncode, result.stdout.strip()) == (3, "QUEUE EMPTY")


def test_forwarding_unavailable_exits_5(tmp_path):
    """AC3: when std cannot be fetched, `ae queue` exits 5 (std's transport code), never 0."""
    result = run(["next"], {"AE_QUEUE_BASE_URL": (tmp_path / "nowhere").as_uri()}, tmp_path)
    assert result.returncode == 5
    assert "work_queue" in result.stderr or "queue_core" in result.stderr


def test_ae_queue_subcommand_forwards(tmp_path, fetched):
    """AC3: the installed `ae` launcher exposes the queue as `ae queue <args>`."""
    env = {"PATH": os.environ["PATH"], "HOME": str(tmp_path / "home"),
           "PYTHONPATH": str(ROOT), "STUB_LOG": str(tmp_path / "calls.log")}
    env.update(fetched)
    result = subprocess.run([sys.executable, "-m", "agent_extensions.install", "queue",
                             "move", "Q03", "--after", "Q01"], env=env,
                            capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "forwarded: move Q03 --after Q01"
    assert calls(tmp_path) == ["move Q03 --after Q01"]


def test_queue_module_is_stdlib_only():
    """AC1: queue.py runs under `python3 -I` straight from a checkout, so it may import only the stdlib."""
    import ast
    tree = ast.parse(QUEUE.read_text(encoding="utf-8"))
    names = {alias.name.split(".")[0] for node in ast.walk(tree)
             if isinstance(node, ast.Import) for alias in node.names}
    names |= {node.module.split(".")[0] for node in ast.walk(tree)
              if isinstance(node, ast.ImportFrom) and node.module}
    assert names <= set(sys.stdlib_module_names) | {"__future__"}
