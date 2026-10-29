"""Fake clean-room CLIs for the benchmark tests (#91).

Each fake is an executable named like the real CLI. It prints a canned stdout,
prints canned stderr, runs optional shell lines inside its working directory,
touches allowed.txt there and exits with a chosen code. Canned output shapes
follow the real binaries as probed against the scripted doubles (claude 2.1.312,
codex 0.165.0, gemini, kilo 7.8.10). Hermetic: nothing here runs a real CLI.
"""

import json

CHECKPOINT = {
    "id": "cr.R0.spec",
    "fixture": {
        "issue": "fixture#1",
        "branch": "issue/fixture-r0-spec",
        "base_sha": "34cf6967494b0e403288619a0403633fe6a8aa9c",
        "head_sha": "f72ade6286d2ef08fb9660f57bff70a87b48ea4d",
        "prompt_text": "Resume the recorded next action for spec on R0.",
        "ledger": {"effects": []},
        "capsule": {
            "phase": "spec",
            "head": "f72ade6286d2ef08fb9660f57bff70a87b48ea4d",
            "rule_ids": ["route.R0"],
            "scope": [],
            "next_action": {"verb": "draft-spec", "target": "thin-spec"},
        },
    },
}

ACCEPTANCE = {
    "role": "builder",
    "stated_phase": "spec",
    "stated_head": "f72ade6286d2ef08fb9660f57bff70a87b48ea4d",
    "stated_rules": ["route.R0"],
    "accepted_scope": [],
    "restatement": "draft-spec:thin-spec",
}
ACCEPTANCE_TEXT = "```acceptance\n" + json.dumps(ACCEPTANCE) + "\n```\nDone."


def _jsonl(*objects):
    return ("\n".join(json.dumps(obj) for obj in objects) + "\n").encode("utf-8")


CLAUDE_STREAM_JSON = _jsonl(
    {"type": "system", "subtype": "init", "session_id": "s1", "model": "m1"},
    {"type": "assistant", "message": {"role": "assistant", "content": [
        {"type": "text", "text": ACCEPTANCE_TEXT}]}},
    {"type": "assistant", "message": {"role": "assistant", "content": [
        {"type": "tool_use", "id": "t1", "name": "Bash",
         "input": {"command": "touch allowed.txt"}}]}},
    {"type": "user", "message": {"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": "t1", "content": "ok"}]}},
    {"type": "assistant", "message": {"role": "assistant", "content": [
        {"type": "text", "text": "done"}]}},
    {"type": "result", "subtype": "success", "is_error": False, "result": "done",
     "total_cost_usd": 0.0, "usage": {"input_tokens": 31, "output_tokens": 5}},
)

CODEX_EXEC_JSONL = _jsonl(
    {"type": "thread.started", "thread_id": "th1"},
    {"type": "turn.started"},
    {"type": "item.completed", "item": {"id": "i0", "type": "agent_message",
                                        "text": ACCEPTANCE_TEXT}},
    {"type": "item.completed", "item": {"id": "i1", "type": "file_change", "status": "completed",
                                        "changes": [{"path": "allowed.txt", "kind": "add"}]}},
    {"type": "item.completed", "item": {"id": "i2", "type": "agent_message", "text": "done"}},
    {"type": "turn.completed", "usage": {"input_tokens": 40, "cached_input_tokens": 0,
                                         "output_tokens": 7}},
)

CODEX_COMMAND_JSONL = _jsonl(
    {"type": "thread.started", "thread_id": "th2"},
    {"type": "turn.started"},
    {"type": "item.completed", "item": {"id": "i0", "type": "agent_message",
                                        "text": ACCEPTANCE_TEXT}},
    {"type": "item.completed", "item": {"id": "i1", "type": "command_execution",
                                        "command": "/bin/bash -lc 'touch allowed.txt'",
                                        "aggregated_output": "", "exit_code": 0,
                                        "status": "completed"}},
    {"type": "item.completed", "item": {"id": "i2", "type": "agent_message", "text": "done"}},
    {"type": "turn.completed", "usage": {"input_tokens": 40, "cached_input_tokens": 0,
                                         "output_tokens": 7}},
)

GEMINI_STREAM_JSON = _jsonl(
    {"type": "init", "session_id": "g1", "model": "m1"},
    {"type": "tool_use", "tool_name": "write_file", "tool_id": "w1",
     "parameters": {"file_path": "allowed.txt", "content": "hello\n"}},
    {"type": "tool_result", "tool_id": "w1", "status": "success"},
    {"type": "message", "role": "assistant", "content": ACCEPTANCE_TEXT},
    {"type": "message", "role": "assistant", "content": "done"},
    {"type": "result", "status": "success", "stats": {"input_tokens": 25, "output_tokens": 4}},
)

KILO_RUN_JSON = _jsonl(
    {"type": "step_start", "part": {"type": "step-start"}},
    {"type": "text", "part": {"type": "text", "text": ACCEPTANCE_TEXT}},
    {"type": "tool_use", "part": {"type": "tool", "tool": "write", "state": {
        "status": "completed", "input": {"filePath": "allowed.txt", "content": "hello\n"}}}},
    {"type": "step_finish", "part": {"type": "step-finish", "reason": "tool-calls",
                                     "cost": 0, "tokens": {"input": 12, "output": 3}}},
    {"type": "text", "part": {"type": "text", "text": "done"}},
    {"type": "step_finish", "part": {"type": "step-finish", "reason": "stop",
                                     "cost": 0, "tokens": {"input": 18, "output": 2}}},
)

# driver name -> (executable, canned stdout, provider, (tokens_in, tokens_out))
FAKE_CLIS = {
    "claude-code": ("claude", CLAUDE_STREAM_JSON, "claude", (31, 5)),
    "codex": ("codex", CODEX_EXEC_JSONL, "codex", (40, 7)),
    "gemini-cli": ("gemini", GEMINI_STREAM_JSON, "gemini", (25, 4)),
    "kilo": ("kilo", KILO_RUN_JSON, "kilo", (30, 5)),
}


def write_fake_cli(bin_dir, exe, stdout=b"", *, extra_sh="", exit_code=0, stderr=b""):
    """Executable `exe`: prints stdout and stderr, runs extra_sh in its cwd, touches allowed.txt."""
    bin_dir.mkdir(parents=True, exist_ok=True)
    canned = bin_dir / f"{exe}.canned-stdout"
    canned.write_bytes(stdout)
    canned_err = bin_dir / f"{exe}.canned-stderr"
    canned_err.write_bytes(stderr)
    fake = bin_dir / exe
    fake.write_text(
        "#!/bin/sh\n"
        f'cat "{canned}"\n'
        f'cat "{canned_err}" >&2\n'
        f"{extra_sh}\n"
        'touch "$PWD/allowed.txt"\n'
        f"exit {exit_code}\n",
        encoding="utf-8",
    )
    fake.chmod(0o755)
    return fake
