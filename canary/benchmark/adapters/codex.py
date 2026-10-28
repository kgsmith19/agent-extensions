"""Codex CLI clean-room driver (#91).

Runs `codex exec --json` inside a clean home, pointed at the scripted Responses
API double. The custom provider is declared with `-c` overrides and
wire_api="responses"; no config.toml is written and no OpenAI key is read.
CODEX_HOME sits inside the clean home.
"""

import hashlib
import json
import os
import shutil
import subprocess
import time
from pathlib import Path

from agent_extensions.sync.harness import ADAPTERS

PROVIDER_ID = "ae_clean_room"
TIMEOUT_S = 900
XDG_DIRS = {
    "XDG_CONFIG_HOME": ".config",
    "XDG_DATA_HOME": ".local/share",
    "XDG_CACHE_HOME": ".cache",
    "XDG_STATE_HOME": ".local/state",
}


class CliNotInstalled(RuntimeError):
    """The codex executable is not on the clean PATH. A declared gap, never a trial."""

    gap_id = "cli-not-installed"


def _prompt(checkpoint):
    fixture = checkpoint.get("fixture", {})
    # The corpus carries fixture.prompt_text. Checkpoints without it get a generic prompt.
    return fixture.get("prompt_text") or f"Continue checkpoint {checkpoint['id']}."


def _env(clean_home):
    env = {
        "PATH": os.environ["PATH"],
        "HOME": str(clean_home),
        "CODEX_HOME": str(clean_home / "codex-home"),
    }
    for var, sub in XDG_DIRS.items():
        env[var] = str(clean_home / sub)
    return env


def _parse_jsonl(raw):
    events = []
    for line in raw.decode("utf-8").splitlines():
        if line.strip():
            events.append(json.loads(line))
    return events


class CodexDriver:
    name = "codex"

    def __init__(self):
        self._checkpoint = None
        self._clean_home = None
        self._alias = None
        self._stdout = None
        self._latency_ms = None

    def prepare(self, checkpoint, clean_home):
        clean_home = Path(clean_home)
        for sub in ("workspace", "codex-home", *XDG_DIRS.values()):
            (clean_home / sub).mkdir(parents=True, exist_ok=True)
        self._checkpoint = checkpoint
        self._clean_home = clean_home
        self._stdout = None

    def command(self, checkpoint, model_base_url, clean_home):
        provider = (
            f'model_providers.{PROVIDER_ID}={{name="{PROVIDER_ID}", '
            f'base_url="{model_base_url}/v1", wire_api="responses", requires_openai_auth=false}}'
        )
        argv = [
            "codex", "exec", "--json", "--skip-git-repo-check", "-s", "workspace-write",
            "-c", f'model_provider="{PROVIDER_ID}"',
            "-c", provider,
            _prompt(checkpoint),
        ]
        return argv, _env(Path(clean_home))

    def run(self, model):
        if self._checkpoint is None:
            raise RuntimeError("prepare() must run before run()")
        argv, env = self.command(self._checkpoint, model["base_url"], self._clean_home)
        if shutil.which(argv[0], path=env["PATH"]) is None:
            raise CliNotInstalled("codex is not on the clean PATH; no trial was run")
        argv = [*argv, "-m", model["alias"]]
        self._alias = model["alias"]
        workspace = self._clean_home / "workspace"
        started = time.monotonic()
        proc = subprocess.run(
            argv, cwd=workspace, env=env, stdin=subprocess.DEVNULL,
            capture_output=True, timeout=TIMEOUT_S, check=False,
        )
        self._latency_ms = int(round((time.monotonic() - started) * 1000))
        self._stdout = proc.stdout

    def collect(self):
        if self._stdout is None:
            raise RuntimeError("run() has not completed; there is no trial to collect")
        events = _parse_jsonl(self._stdout)
        turns = [e for e in events if e.get("type") == "turn.completed"]
        if not turns:
            raise RuntimeError("codex produced no turn.completed event; the run did not finish")
        items = [e["item"] for e in events if e.get("type") == "item.completed" and "item" in e]
        actions = [i for i in items if i.get("type") in ("file_change", "command_execution")]

        files_touched = []
        for item in items:
            if item.get("type") == "file_change" and item.get("status") == "completed":
                for change in item.get("changes", []):
                    if change["path"] not in files_touched:
                        files_touched.append(change["path"])

        return {
            "harness": self.name,
            "provider": "codex",
            "model_alias": self._alias,
            "checkpoint_id": self._checkpoint["id"],
            "first_action": self._first_action(actions[0]) if actions else {"verb": "none", "target": ""},
            "files_touched": files_touched,
            "tokens_in": sum(t["usage"]["input_tokens"] for t in turns),
            "tokens_out": sum(t["usage"]["output_tokens"] for t in turns),
            "latency_ms": self._latency_ms,
            "cost_usd": None,
            "interventions": 0,
            "raw_log_digest": "sha256:" + hashlib.sha256(self._stdout).hexdigest(),
        }

    @staticmethod
    def _first_action(item):
        if item["type"] == "command_execution":
            return {"verb": "exec", "target": item["command"]}
        kind_to_verb = {"add": "write", "update": "edit", "delete": "delete"}
        change = item["changes"][0]
        return {"verb": kind_to_verb[change["kind"]], "target": change["path"]}

    def gaps(self):
        adapter = ADAPTERS["codex"]
        return [{"id": gap, "reason": adapter.gap_reason} for gap in adapter.gaps()]
