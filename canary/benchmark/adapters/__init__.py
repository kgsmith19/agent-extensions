"""Clean-room harness drivers, keyed by harness name (#91).

DRIVERS maps each harness name to its driver class; every class is built with no
arguments. The codex and gemini-cli drivers come from parallel work (C2). They are
imported under a guard that catches only the absence of their own module.
"""

from canary.benchmark.adapters.claude_code import ClaudeCodeDriver
from canary.benchmark.adapters.kilo import KiloDriver

DRIVERS = {
    "claude-code": ClaudeCodeDriver,
    "kilo": KiloDriver,
}

# TODO(integration): remove this guard at merge; codex.py and gemini_cli.py are required.
try:
    from canary.benchmark.adapters.codex import CodexDriver
except ModuleNotFoundError as exc:
    if exc.name != "canary.benchmark.adapters.codex":
        raise
else:
    DRIVERS["codex"] = CodexDriver

# TODO(integration): remove this guard at merge; codex.py and gemini_cli.py are required.
try:
    from canary.benchmark.adapters.gemini_cli import GeminiCliDriver
except ModuleNotFoundError as exc:
    if exc.name != "canary.benchmark.adapters.gemini_cli":
        raise
else:
    DRIVERS["gemini-cli"] = GeminiCliDriver
