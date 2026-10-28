"""Claude Code driver for the clean-room benchmark (#91).

Runs `claude -p --output-format stream-json --bare --verbose` against the scripted
model double. Real flags verified on claude 2.1.312: stream-json with -p requires
--verbose, and --bare limits the tool set to Bash, Edit and Read.
"""

from canary.benchmark.adapters._base import (
    CANARY_API_KEY,
    TASK_PROMPT,
    BaseDriver,
    RunSummary,
    action_target,
    clean_env,
    parse_json_lines,
)

CACHE_TOKEN_KEYS = ("cache_creation_input_tokens", "cache_read_input_tokens")


class ClaudeCodeDriver(BaseDriver):
    name = "claude-code"
    provider = "claude"
    exe = "claude"
    # --bare skips hooks, so the hooks a user has configured never run in the clean room.
    extra_gaps = (
        ("hooks-not-run", "--bare skips hooks: no user or project hook runs in the clean room"),
    )

    def command(self, checkpoint, model_base_url, clean_home):
        argv = [self.exe, "-p", "--output-format", "stream-json", "--bare", "--verbose",
                "--permission-mode", "acceptEdits", TASK_PROMPT]
        env = clean_env(clean_home, {"ANTHROPIC_BASE_URL": model_base_url,
                                     "ANTHROPIC_API_KEY": CANARY_API_KEY})
        return argv, env

    def argv_for_model(self, argv, alias):
        # The prompt stays last; the model flag goes just before it.
        return [*argv[:-1], "--model", alias, argv[-1]]

    def parse_log(self, stdout):
        events = parse_json_lines(stdout)
        actions = []
        for event in events:
            if event.get("type") != "assistant":
                continue
            for block in (event.get("message") or {}).get("content") or []:
                if block.get("type") == "tool_use":
                    actions.append((block["name"].lower(), action_target(block.get("input") or {})))
        results = [event for event in events if event.get("type") == "result"]
        if not results:
            raise ValueError("claude stream-json log has no result event")
        result = results[-1]
        if result.get("is_error"):
            raise ValueError(f"claude result event reports is_error ({result.get('subtype')})")
        usage = result["usage"]
        tokens_in = usage["input_tokens"] + sum(usage.get(key, 0) for key in CACHE_TOKEN_KEYS)
        cost = result.get("total_cost_usd")
        return RunSummary(actions=actions, tokens_in=tokens_in,
                          tokens_out=usage["output_tokens"],
                          cost_usd=None if cost is None else float(cost))
