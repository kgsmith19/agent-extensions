<!-- managed-by: agent-extensions -->

# pi Global Instructions

Follow, in order:
1. Current owner instruction.
2. Project instructions in the current repo.
3. `~\AGENTS.md`.
4. Agent harness config in `~/.config/agents\`.
5. pi defaults.

pi-specific notes:
- Skills load from `~/.agents/skills` (provider-neutral capability bundle); project-local `.pi/` resources load only after the project is trusted.
- Extensions: the continuity adapter (`agent_extensions/continuity/adapters/pi-extension.ts`) injects the project capsule at session start; keep it provider-neutral.
- Keep pi auth/settings in `~/.pi\`; keep portable non-secret agent config in `~/.config/agents\`.
- Before claiming success, run the relevant check and report the exact command and result.
