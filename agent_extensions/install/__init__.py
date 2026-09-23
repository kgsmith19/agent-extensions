"""Forceful, idempotent machine + repo agent setup (issue #53).

Canonical installer for the one-line bootstrap: machine stages (gitignore,
shims, hooks, cli-shim) plus forceful repo-mode injection (overlay, bindings,
guards). Spec: docs/superpowers/specs/2026-09-23-one-line-agent-setup-design.md
"""

MANAGED_MD_MARKER = "<!-- managed-by: agent-extensions -->"
GITIGNORE_NOTE = "# agent harness local overlays — never committed in any repo"
GITIGNORE_ENTRIES = [
    "AGENTS.local.md",
    "AGENTS.override.md",
    "**/.claude/settings.local.json",
    "**/.pi/settings.json",
    "**/AGENTS.local.d/",
]
