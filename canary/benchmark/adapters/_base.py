"""Shared plumbing for the clean-room benchmark drivers (#91).

Every driver module in this package builds on BaseDriver. Public API:

  CLI_NOT_INSTALLED                    gap id for a missing CLI ("cli-not-installed").
  CliNotInstalled(exe)                 Raised by run() when the CLI is not on PATH.
                                       Carries gap_id; a declared gap, never a trial.
  CliRunFailed(exe, returncode, tail)  Raised by run() on a non-zero exit.
  TASK_PROMPT                          The resume prompt every CLI receives.
  CANARY_API_KEY                       Non-secret placeholder for CLIs that demand a key.
  clean_env(clean_home, extra=None)    Env built from scratch: PATH, LANG, NO_COLOR, TMPDIR,
                                       HOME and XDG_* inside clean_home, then `extra`.
                                       No other inherited variable passes, so real
                                       *_API_KEY values never reach the CLI.
  digest(data: bytes) -> "sha256:<hex>"  The raw_log_digest of the captured stdout bytes.
  digest_tree(root) -> {relpath: hex}  Snapshot used to derive files_touched.
  parse_json_lines(stdout: bytes) -> list[dict]
                                       Raises ValueError on a line that is not JSON and
                                       TypeError on a JSON line that is not an object;
                                       both name the line number.
  action_target(tool_input) -> str     First of file_path, filePath, path, command, else "".
  RunSummary(actions, tokens_in, tokens_out, cost_usd)
                                       What parse_log() returns. actions is a list of
                                       (verb, target) tuples in call order.
  BaseDriver                           The protocol every driver implements:
    class attributes  name (== its DRIVERS key), provider, exe,
                      extra_gaps: ((id, reason), ...) beyond the harness adapter's gaps.
    command(checkpoint, model_base_url, clean_home) -> (argv, env)   ABSTRACT, pure.
    parse_log(stdout: bytes) -> RunSummary                           ABSTRACT.
    prepare_home(clean_home, model) -> None    Hook, default no-op; runs just before exec.
    argv_for_model(argv, alias) -> list        Hook, default returns argv unchanged.
    prepare(checkpoint, clean_home)            Creates clean_home/workspace, snapshots it.
    run(model)                                 model = {"alias", "base_url"}; execs the CLI
                                               in the workspace; times it; keeps stdout.
    collect() -> trial dict                    Trial fields from the log and the workspace.
    gaps() -> [{"id", "reason"}]               Harness adapter gaps plus extra_gaps.

Drivers must not read real *_API_KEY values, and must build env with clean_env().
"""

import hashlib
import json
import os
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path

from agent_extensions.sync.harness import ADAPTERS

CLI_NOT_INSTALLED = "cli-not-installed"
TASK_PROMPT = "Resume the recorded next action and write allowed.txt containing hello."
CANARY_API_KEY = "scripted-canary-no-secret"
RUN_TIMEOUT_S = 900
STDERR_TAIL_CHARS = 2000


class CliNotInstalled(Exception):
    """The CLI is not on PATH: a declared gap, never a trial."""

    gap_id = CLI_NOT_INSTALLED

    def __init__(self, exe):
        super().__init__(f"{exe}: not installed (not on PATH)")
        self.exe = exe


class CliRunFailed(Exception):
    """The CLI exited non-zero; nothing is collected from it."""

    def __init__(self, exe, returncode, stderr_tail):
        super().__init__(f"{exe} exited {returncode}: {stderr_tail}")
        self.exe = exe
        self.returncode = returncode
        self.stderr_tail = stderr_tail


@dataclass(frozen=True)
class RunSummary:
    actions: list = field(default_factory=list)  # [(verb, target)] in call order
    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: float | None = None


def clean_env(clean_home, extra=None):
    """Environment built from scratch: nothing is inherited except PATH."""
    home = str(clean_home)
    env = {
        "PATH": os.environ["PATH"],
        "HOME": home,
        "TMPDIR": home,
        "LANG": "C.UTF-8",
        "NO_COLOR": "1",
        "XDG_CONFIG_HOME": os.path.join(home, ".config"),
        "XDG_DATA_HOME": os.path.join(home, ".local", "share"),
        "XDG_STATE_HOME": os.path.join(home, ".local", "state"),
        "XDG_CACHE_HOME": os.path.join(home, ".cache"),
    }
    env.update(extra or {})
    return env


def digest(data):
    return "sha256:" + hashlib.sha256(data).hexdigest()


def digest_tree(root):
    root = Path(root)
    tree = {}
    for path in sorted(root.rglob("*")):
        if path.is_file():
            tree[path.relative_to(root).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    return tree


def parse_json_lines(stdout):
    events = []
    for number, line in enumerate(stdout.decode("utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except ValueError as exc:
            raise ValueError(f"line {number}: not JSON") from exc
        if not isinstance(value, dict):
            raise TypeError(f"line {number}: not a JSON object")
        events.append(value)
    return events


def action_target(tool_input):
    for key in ("file_path", "filePath", "path", "command"):
        if tool_input.get(key):
            return tool_input[key]
    return ""


class BaseDriver:
    name = ""
    provider = ""
    exe = ""
    extra_gaps = ()

    def __init__(self):
        self._checkpoint = None
        self._clean_home = None
        self._workspace = None
        self._before = None
        self._stdout = None
        self._latency_ms = None
        self._alias = None

    # --- abstract -----------------------------------------------------------------------

    def command(self, checkpoint, model_base_url, clean_home):
        raise NotImplementedError

    def parse_log(self, stdout):
        raise NotImplementedError

    # --- hooks --------------------------------------------------------------------------

    def prepare_home(self, clean_home, model):
        return None

    def argv_for_model(self, argv, alias):
        return list(argv)

    # --- protocol -----------------------------------------------------------------------

    def prepare(self, checkpoint, clean_home):
        self._checkpoint = checkpoint
        self._clean_home = Path(clean_home)
        self._workspace = self._clean_home / "workspace"
        self._workspace.mkdir(parents=True, exist_ok=True)
        self._before = digest_tree(self._workspace)
        self._stdout = None

    def run(self, model):
        if self._workspace is None:
            raise RuntimeError(f"{self.name}: prepare() must run before run()")
        alias, base_url = model["alias"], model["base_url"]
        argv, env = self.command(self._checkpoint, base_url, self._clean_home)
        if shutil.which(self.exe, path=env["PATH"]) is None:
            raise CliNotInstalled(self.exe)
        self.prepare_home(self._clean_home, model)
        argv = self.argv_for_model(argv, alias)
        started = time.monotonic()
        proc = subprocess.run(argv, cwd=self._workspace, env=env, capture_output=True,
                              timeout=RUN_TIMEOUT_S, check=False)
        self._latency_ms = round((time.monotonic() - started) * 1000)
        if proc.returncode != 0:
            tail = proc.stderr.decode("utf-8", errors="replace")[-STDERR_TAIL_CHARS:]
            raise CliRunFailed(self.exe, proc.returncode, tail)
        self._stdout = proc.stdout
        self._alias = alias

    def collect(self):
        if self._stdout is None:
            raise RuntimeError(f"{self.name}: run() must succeed before collect()")
        summary = self.parse_log(self._stdout)
        after = digest_tree(self._workspace)
        touched = sorted(p for p, h in after.items() if self._before.get(p) != h)
        actions = summary.actions
        first = {"verb": actions[0][0], "target": actions[0][1]} if actions else {
            "verb": "", "target": ""}
        return {
            "harness": self.name,
            "provider": self.provider,
            "model_alias": self._alias,
            "checkpoint_id": self._checkpoint["id"],
            "first_action": first,
            "effects": [f"{verb}:{target}" for verb, target in actions],
            "files_touched": touched,
            "tokens_in": summary.tokens_in,
            "tokens_out": summary.tokens_out,
            "latency_ms": self._latency_ms,
            "cost_usd": summary.cost_usd,
            "interventions": 0,
            "raw_log_digest": digest(self._stdout),
        }

    def gaps(self):
        harness = ADAPTERS[self.name]
        declared = [{"id": gap, "reason": harness.gap_reason} for gap in harness.gaps()]
        declared += [{"id": gap, "reason": reason} for gap, reason in self.extra_gaps]
        return declared
