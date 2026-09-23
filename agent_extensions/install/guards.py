"""Compliance guard (issue #53): pure violation finder + Claude hook entrypoint.

Deny classes v1 (narrow by design):
1. secret-shaped values in Bash commands or Write/Edit content
2. git push targeting a protected branch (main/master)

The guard fails OPEN (crash -> allow) and never echoes matched secret text.
"""

from __future__ import annotations

import json
import re
import sys

SECRET_PATTERNS = [
    re.compile(r"sk-ant-[A-Za-z0-9\-_]{20,}"),
    re.compile(r"ghp_[A-Za-z0-9]{20,}"),
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
]

# git push ... main|master — but not feature/main, not main-thing, not origin/main-thing
PROTECTED_PUSH = re.compile(
    r"git\s+push\b[^|;&]*(?<![/\w.-])(?:HEAD:)?(main|master)(?![\w-])"
)

VIOLATION_SECRET = (
    "BLOCKED: secret-shaped value detected — per Global Agent Rules, secrets live "
    "only in the secret provider. Remove the secret from this input; if a key leaked, "
    "rotate it at the provider."
)
VIOLATION_PUSH = (
    "BLOCKED: direct push to a protected branch (main/master) — per Global Agent Rules, "
    "work lands via feature branch + PR; ask the owner for an explicit override if this "
    "push is intentional."
)


def find_violations(tool_name: str, tool_input: dict) -> list[str]:
    """Pure check: return human-readable violations (never secret text)."""
    if tool_name not in ("Bash", "Write", "Edit"):
        return []
    haystacks = []
    if tool_name == "Bash":
        haystacks.append(str(tool_input.get("command", "")))
    else:
        for key in ("content", "command", "new_string"):
            if tool_input.get(key):
                haystacks.append(str(tool_input[key]))
    violations: list[str] = []
    for text in haystacks:
        for pattern in SECRET_PATTERNS:
            if pattern.search(text):
                violations.append(VIOLATION_SECRET)
                break
        if tool_name == "Bash" and PROTECTED_PUSH.search(text):
            violations.append(VIOLATION_PUSH)
    return violations


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if "--selfcheck" in args:
        checks = [
            find_violations("Bash", {"command": "git push origin main"}),
            find_violations("Bash", {"command": "echo sk-ant-" + "a" * 24}),
            find_violations("Bash", {"command": "git push origin feature/x"}),
        ]
        if checks[0] and checks[1] and not checks[2]:
            print("guard selfcheck ok")
            return 0
        print("guard selfcheck FAILED", file=sys.stderr)
        return 1
    try:
        payload = json.load(sys.stdin)
        violations = find_violations(
            str(payload.get("tool_name", "")), payload.get("tool_input") or {}
        )
    except Exception:
        return 0  # fail open — a broken guard never blocks a session
    if violations:
        for v in violations:
            print(v, file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
