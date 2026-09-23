<!-- managed-by: agent-extensions -->

# Claude Code Global Instructions

Follow, in order:
1. Current owner instruction.
2. Project instructions in the current repo.
3. `~\AGENTS.md`.
4. Agent harness config in `~/.config/agents\`.
5. Claude Code defaults.

Claude-specific notes:
- Load Superpowers skills when they match the task; prefer Superpowers process skills over overlapping narrower guidance.
- Use Explore/subagents for read-only discovery when useful, but implement and verify in the main loop unless explicitly delegated.
- Keep harness-specific credentials, plugins, and settings in Claude settings or `~/.config/agents\`; never in repos.
- Before claiming success, run the relevant check and report the exact command and result.
