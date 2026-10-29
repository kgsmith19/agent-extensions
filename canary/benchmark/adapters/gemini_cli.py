"""Gemini CLI clean-room driver (#91).

Runs `gemini -p <prompt> --output-format stream-json` inside a clean home. The
API base comes from GOOGLE_GEMINI_BASE_URL and the auth type is pinned in the
clean home's settings, so no real Gemini key is read. The surface this harness
exposes is not declared in ADAPTERS, so gaps() reports it as undeclared.
"""

import json
from pathlib import Path

from canary.benchmark.adapters._base import (
    BaseDriver,
    RunSummary,
    action_target,
    clean_env,
    parse_json_lines,
)
from canary.benchmark.core import RunIncomplete, build_prompt

PLACEHOLDER_KEY = "clean-room-placeholder-not-a-secret"
WRITE_TOOLS = {"write_file": "write", "replace": "edit"}
XDG_DIRS = (".config", ".local/share", ".cache", ".local/state")


class GeminiCliDriver(BaseDriver):
    name = "gemini-cli"
    provider = "gemini"
    exe = "gemini"

    def command(self, checkpoint, model_base_url, clean_home):
        argv = [
            self.exe, "-p", build_prompt(checkpoint),
            "--output-format", "stream-json",
            "--approval-mode", "yolo", "--skip-trust",
        ]
        env = clean_env(Path(clean_home), {"GOOGLE_GEMINI_BASE_URL": model_base_url,
                                           "GEMINI_API_KEY": PLACEHOLDER_KEY})
        return argv, env

    def prepare_home(self, clean_home, model):
        home = Path(clean_home)
        for sub in (".gemini", *XDG_DIRS):
            (home / sub).mkdir(parents=True, exist_ok=True)
        (home / ".gemini" / "settings.json").write_text(
            json.dumps({"security": {"auth": {"selectedType": "gemini-api-key"}}}),
            encoding="utf-8",
        )

    def argv_for_model(self, argv, alias):
        return [*argv, "-m", alias]

    def parse_log(self, stdout):
        events = parse_json_lines(stdout)
        results = [e for e in events if e.get("type") == "result"]
        if not results:
            raise RunIncomplete("gemini produced no result event; the run did not finish")
        stats = results[-1]["stats"]
        actions = [(WRITE_TOOLS.get(e["tool_name"], e["tool_name"]),
                    action_target(e.get("parameters") or {}))
                   for e in events if e.get("type") == "tool_use"]
        texts = [e["content"] for e in events
                 if e.get("type") == "message" and e.get("role") == "assistant"]
        return RunSummary(actions=actions, tokens_in=stats["input_tokens"],
                          tokens_out=stats["output_tokens"], transcript_text="\n".join(texts))

    def gaps(self):
        return [{
            "id": "harness-surface-undeclared",
            "reason": (
                "gemini-cli has no entry in agent_extensions.sync.harness.ADAPTERS, so its hooks, "
                "extensions and sandbox surface are not declared; nothing about them is assumed"
            ),
        }]
