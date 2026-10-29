"""Clean-room harness drivers, keyed by harness name (#91).

DRIVERS maps each harness name to its driver class; every class is built with no
arguments.
"""

from canary.benchmark.adapters.claude_code import ClaudeCodeDriver
from canary.benchmark.adapters.codex import CodexDriver
from canary.benchmark.adapters.gemini_cli import GeminiCliDriver
from canary.benchmark.adapters.kilo import KiloDriver

DRIVERS = {
    "claude-code": ClaudeCodeDriver,
    "codex": CodexDriver,
    "gemini-cli": GeminiCliDriver,
    "kilo": KiloDriver,
}
