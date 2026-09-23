<!-- managed-by: agent-extensions -->

# Codex Global Instructions

Follow, in order:
1. Current owner instruction.
2. Project instructions in the current repo.
3. `~\AGENTS.md`.
4. Agent harness config in `~/.config/agents\`.
5. Codex defaults.

Codex-specific notes:
- Use available skills/plugins when they match the task; prefer Superpowers process skills when present.
- Use read-only exploration agents/tools for discovery when useful; implement and verify in the main loop unless explicitly delegated.
- Keep Codex auth/settings in `~\.codex\`; keep portable non-secret agent config in `~/.config/agents\`.
- Before claiming success, run the relevant check and report the exact command and result.
