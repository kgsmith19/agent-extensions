"""Kilo driver for the clean-room benchmark (#91).

Runs `kilo run --format json --auto <prompt>` against the scripted model double.
The provider block and the kilo.json rendering are imported from the canary runner
(canary/kilo/run_canary.py), not copied. Output shapes are checked against the real
kilo 7.8.10 run and tests/fixtures/kilo.
"""

import copy
import json
from pathlib import Path

from canary.benchmark.adapters._base import (
    TASK_PROMPT,
    BaseDriver,
    RunSummary,
    action_target,
    clean_env,
    parse_json_lines,
)
from canary.kilo.run_canary import PROVIDER_BLOCK, TEMPLATE, render_config


class KiloDriver(BaseDriver):
    name = "kilo"
    provider = "kilo"
    exe = "kilo"

    def command(self, checkpoint, model_base_url, clean_home):
        argv = [self.exe, "run", "--format", "json", "--auto", TASK_PROMPT]
        # kilo.json (written by prepare_home) carries the base URL literally, as the canary
        # does. This variable repeats it for the OpenAI-wire contract; kilo does not read it.
        env = clean_env(clean_home, {"OPENAI_BASE_URL": f"{model_base_url}/v1"})
        return argv, env

    def prepare_home(self, clean_home, model):
        provider = copy.deepcopy(PROVIDER_BLOCK)
        provider["scripted"]["options"]["baseURL"] = f"{model['base_url']}/v1"
        rendered = render_config(TEMPLATE.read_text(encoding="utf-8"),
                                 provider=provider, model=model["alias"])
        config_dir = Path(clean_home) / ".config" / "kilo"
        config_dir.mkdir(parents=True, exist_ok=True)
        (config_dir / "kilo.json").write_text(json.dumps(rendered, indent=2), encoding="utf-8")

    def parse_log(self, stdout):
        actions = []
        tokens_in = tokens_out = 0
        costs = []
        steps = 0
        for event in parse_json_lines(stdout):
            part = event.get("part") or {}
            if event.get("type") == "tool_use":
                state = part.get("state") or {}
                actions.append((part["tool"], action_target(state.get("input") or {})))
            elif event.get("type") == "step_finish":
                steps += 1
                tokens = part["tokens"]
                tokens_in += tokens["input"]
                tokens_out += tokens["output"]
                costs.append(part["cost"])
        if steps == 0:
            raise ValueError("kilo json log has no step_finish event")
        return RunSummary(actions=actions, tokens_in=tokens_in, tokens_out=tokens_out,
                          cost_usd=float(sum(costs)))
