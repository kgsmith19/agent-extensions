"""Gemini CLI clean-room driver (#91).

Runs `gemini -p <prompt> --output-format stream-json` inside a clean home. The
API base comes from GOOGLE_GEMINI_BASE_URL and the auth type is pinned in the
clean home's settings, so no real Gemini key is read. The surface this harness
exposes is not declared in ADAPTERS, so gaps() reports it as undeclared.
"""

import hashlib
import json
import os
import shutil
import subprocess
import time
from pathlib import Path

PLACEHOLDER_KEY = "clean-room-placeholder-not-a-secret"
TIMEOUT_S = 900
WRITE_TOOLS = {"write_file": "write", "replace": "edit"}
XDG_DIRS = {
    "XDG_CONFIG_HOME": ".config",
    "XDG_DATA_HOME": ".local/share",
    "XDG_CACHE_HOME": ".cache",
    "XDG_STATE_HOME": ".local/state",
}


class CliNotInstalled(RuntimeError):
    """The gemini executable is not on the clean PATH. A declared gap, never a trial."""

    gap_id = "cli-not-installed"


def _prompt(checkpoint):
    fixture = checkpoint.get("fixture", {})
    # The corpus carries fixture.prompt_text. Checkpoints without it get a generic prompt.
    return fixture.get("prompt_text") or f"Continue checkpoint {checkpoint['id']}."


def _env(clean_home, model_base_url):
    env = {
        "PATH": os.environ["PATH"],
        "HOME": str(clean_home),
        "GOOGLE_GEMINI_BASE_URL": model_base_url,
        "GEMINI_API_KEY": PLACEHOLDER_KEY,
    }
    for var, sub in XDG_DIRS.items():
        env[var] = str(clean_home / sub)
    return env


def _parse_stream(raw):
    events = []
    for line in raw.decode("utf-8").splitlines():
        if line.strip():
            events.append(json.loads(line))
    return events


class GeminiCliDriver:
    name = "gemini-cli"

    def __init__(self):
        self._checkpoint = None
        self._clean_home = None
        self._alias = None
        self._stdout = None
        self._latency_ms = None

    def prepare(self, checkpoint, clean_home):
        clean_home = Path(clean_home)
        for sub in ("workspace", ".gemini", *XDG_DIRS.values()):
            (clean_home / sub).mkdir(parents=True, exist_ok=True)
        (clean_home / ".gemini" / "settings.json").write_text(
            json.dumps({"security": {"auth": {"selectedType": "gemini-api-key"}}}),
            encoding="utf-8",
        )
        self._checkpoint = checkpoint
        self._clean_home = clean_home
        self._stdout = None

    def command(self, checkpoint, model_base_url, clean_home):
        argv = [
            "gemini", "-p", _prompt(checkpoint),
            "--output-format", "stream-json",
            "--approval-mode", "yolo", "--skip-trust",
        ]
        return argv, _env(Path(clean_home), model_base_url)

    def run(self, model):
        if self._checkpoint is None:
            raise RuntimeError("prepare() must run before run()")
        argv, env = self.command(self._checkpoint, model["base_url"], self._clean_home)
        if shutil.which(argv[0], path=env["PATH"]) is None:
            raise CliNotInstalled("gemini is not on the clean PATH; no trial was run")
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
        events = _parse_stream(self._stdout)
        results = [e for e in events if e.get("type") == "result"]
        if not results:
            raise RuntimeError("gemini produced no result event; the run did not finish")
        stats = results[-1]["stats"]
        tool_uses = [e for e in events if e.get("type") == "tool_use"]
        statuses = {e["tool_id"]: e["status"] for e in events if e.get("type") == "tool_result"}

        files_touched = []
        for use in tool_uses:
            path = use["parameters"].get("file_path")
            if use["tool_name"] in WRITE_TOOLS and statuses.get(use["tool_id"]) == "success":
                if path not in files_touched:
                    files_touched.append(path)

        return {
            "harness": self.name,
            "provider": "gemini",
            "model_alias": self._alias,
            "checkpoint_id": self._checkpoint["id"],
            "first_action": self._first_action(tool_uses[0]) if tool_uses else {"verb": "none", "target": ""},
            "files_touched": files_touched,
            "tokens_in": stats["input_tokens"],
            "tokens_out": stats["output_tokens"],
            "latency_ms": self._latency_ms,
            "cost_usd": None,
            "interventions": 0,
            "raw_log_digest": "sha256:" + hashlib.sha256(self._stdout).hexdigest(),
        }

    @staticmethod
    def _first_action(use):
        params = use["parameters"]
        verb = WRITE_TOOLS.get(use["tool_name"], use["tool_name"])
        return {"verb": verb, "target": params.get("file_path", params.get("command", use["tool_name"]))}

    def gaps(self):
        return [{
            "id": "harness-surface-undeclared",
            "reason": (
                "gemini-cli has no entry in agent_extensions.sync.harness.ADAPTERS, so its hooks, "
                "extensions and sandbox surface are not declared; nothing about them is assumed"
            ),
        }]
