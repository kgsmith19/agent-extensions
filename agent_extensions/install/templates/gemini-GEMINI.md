<!-- managed-by: agent-extensions -->

# Gemini / Antigravity Global Instructions

Follow, in order:
1. Current owner instruction.
2. Project instructions in the current repo.
3. `~\AGENTS.md`.
4. Agent harness config in `~/.config/agents\`.
5. Gemini defaults.

Gemini-specific notes:
- Prefer AGENTS.md as the context file (`contextFileName: ["AGENTS.md", "GEMINI.md"]`) so project instructions stay provider-neutral.
- Skills load from `~/.agents/skills` via the agent-extensions sync; plugin folders live in `~/.gemini/config/plugins`.
- Keep Gemini auth/settings in `~\.gemini\`; keep portable non-secret agent config in `~/.config/agents\`.
- Before claiming success, run the relevant check and report the exact command and result.
