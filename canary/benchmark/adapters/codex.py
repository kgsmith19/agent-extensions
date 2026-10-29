"""Codex CLI clean-room driver (#91).

Runs `codex exec --json` inside a clean home, pointed at the scripted Responses
API double. The custom provider is declared with `-c` overrides and
wire_api="responses"; no config.toml is written and no OpenAI key is read.
CODEX_HOME sits inside the clean home.
"""

from pathlib import Path

from canary.benchmark.adapters._base import (
    BaseDriver,
    RunSummary,
    clean_env,
    parse_json_lines,
)
from canary.benchmark.core import RunIncomplete, build_prompt

PROVIDER_ID = "ae_clean_room"
XDG_DIRS = (".config", ".local/share", ".cache", ".local/state")
KIND_TO_VERB = {"add": "write", "update": "edit", "delete": "delete"}


class CodexDriver(BaseDriver):
    name = "codex"
    provider = "codex"
    exe = "codex"

    def command(self, checkpoint, model_base_url, clean_home):
        provider = (
            f'model_providers.{PROVIDER_ID}={{name="{PROVIDER_ID}", '
            f'base_url="{model_base_url}/v1", wire_api="responses", requires_openai_auth=false}}'
        )
        argv = [
            self.exe, "exec", "--json", "--skip-git-repo-check", "-s", "workspace-write",
            "-c", f'model_provider="{PROVIDER_ID}"',
            "-c", provider,
            build_prompt(checkpoint),
        ]
        env = clean_env(Path(clean_home), {"CODEX_HOME": str(Path(clean_home) / "codex-home")})
        return argv, env

    def prepare_home(self, clean_home, model):
        for sub in ("codex-home", *XDG_DIRS):
            (Path(clean_home) / sub).mkdir(parents=True, exist_ok=True)

    def argv_for_model(self, argv, alias):
        # The prompt stays last; the model flag goes just before it.
        return [*argv[:-1], "-m", alias, argv[-1]]

    def parse_log(self, stdout):
        events = parse_json_lines(stdout)
        turns = [e for e in events if e.get("type") == "turn.completed"]
        if not turns:
            raise RunIncomplete("codex produced no turn.completed event; the run did not finish")
        actions = []
        texts = []
        for event in events:
            if event.get("type") != "item.completed" or "item" not in event:
                continue
            item = event["item"]
            if item.get("type") == "agent_message":
                texts.append(item["text"])
            elif item.get("type") == "command_execution":
                actions.append(("exec", item["command"]))
            elif item.get("type") == "file_change":
                actions.extend((KIND_TO_VERB[change["kind"]], change["path"])
                               for change in item.get("changes", []))
        return RunSummary(
            actions=actions,
            tokens_in=sum(t["usage"]["input_tokens"] for t in turns),
            tokens_out=sum(t["usage"]["output_tokens"] for t in turns),
            transcript_text="\n".join(texts),
        )
