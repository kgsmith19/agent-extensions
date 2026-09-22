#!/usr/bin/env python
"""Claude Code hook adapter for the provider-neutral continuity capsules.

Claude Code pipes a JSON event to stdin. This adapter bridges it to the
neutral capsule CLI and prints the result in the shape Claude expects:

- ``SessionStart`` -> capsule rendered as ``hookSpecificOutput.additionalContext``
- ``PreCompact``   -> capsule captured from auto-detected git state

The adapter is intentionally the only Claude-aware file; the store, schema,
and CLI stay provider-neutral.
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from agent_extensions.continuity.__main__ import main as capsule_main  # noqa: E402


def run(payload: dict) -> int:
    event = payload.get("hook_event_name", "")
    cwd = payload.get("cwd") or "."
    if event == "SessionStart":
        return capsule_main(["render", "--format", "claude", "--cwd", cwd])
    if event == "PreCompact":
        return capsule_main(["capture", "--auto", "--cwd", cwd])
    return 0


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except Exception:
        return 0
    try:
        return run(payload)
    except Exception:
        # A continuity failure must never break the session.
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
