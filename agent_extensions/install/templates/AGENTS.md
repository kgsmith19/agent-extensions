<!-- managed-by: agent-extensions -->

# Global Agent Rules

These rules apply to every local harness unless a project file or direct owner instruction says otherwise. They are optimized for software engineering work.

## Precedence
1. Current owner instruction.
2. Project instructions: `AGENTS.md`, `CLAUDE.md`, `GEMINI.md`, `project.yaml`, issues, plans.
3. This file.
4. Harness defaults.

If rules conflict, follow the higher source and mention the conflict once.

## Startup
- Start in `~/code/<repo>` when working on repositories. If elsewhere, find the repo root before editing.
- Read project instructions before changing files.
- Use available skills/plugins when they match the task. Prefer Superpowers process skills when present.
- For non-trivial changes, isolate work in the harness's native worktree support, or use `.worktrees/` under the repo.

## Environment bootstrap (self-healing)
- If this environment lacks the capability layer (`~/.agents/skills` missing/empty) or a harness shim (`~/.pi/agent/AGENTS.md`, `~/.claude/CLAUDE.md`, `~/.codex/AGENTS.md`, `~/.gemini/GEMINI.md`), provision it idempotently:
  `curl -fsSL https://raw.githubusercontent.com/kgsmith19/agent-extensions/main/bootstrap.sh | bash`
- The provisioned layer is machine-global and applies to every repo; never commit it into a repository.
- Baseline vs project: this file is the baseline; the repo's own `AGENTS.md` is project-specific and loads after it, so project instructions win where they differ.
- Repo adoption of the full guardrail set is explicit and committed: `python tools/standardctl.py init` in the target repo (renders templates + `standard.lock`).

## Session continuity
- Keep the project's continuity capsule current as work progresses:
  `python ~/code\agent-extensions\agent_extensions\continuity\adapters\capsule.py capture --task "<what>" --next "<next action>" [--phase <phase>] [--blocker "<b>"] [--issue <n>]`.
- The capsule is injected at session start and refreshed before compaction; a fresh session must be able to resume from it without re-deriving context.
- Store is provider-neutral: `~/.agent-state/capsules/<project>.json`. Read it with `... render`; never hand-edit a provider's own memory file.

## Parallel execution (worktrees + subagents) — aggressive by default
- Parallelize aggressively: independent issues in separate worktrees with focused subagents are the DEFAULT, not the exception. Sequential execution needs a reason (shared files, shared schema, dependent outputs, same contract/migration). When uncertain, parallelize and let the controller verify overlap at integration.
- Worktree usage (mandatory scale): one isolated worktree per active issue (`<repo>/.worktrees/issue-<n>-<slug>`, branch `issue/<n>-<slug>`, claimed atomically via the repo-provider ref API — "already exists" means claimed, move on). Keep up to ~6 concurrent worktrees per repo when issues are independent; read-only research/critique/verification agents may inspect concurrently when the harness guarantees no mutation.
- Subagent usage (mandatory scale): dispatch one focused writer subagent per worktree with a tight brief (issue, branch, exact head, file boundary, acceptance criteria, verification commands) plus independent reviewer/verifier subagents for R2/R3 work. Fresh subagent per task; report files over pasted history; checkpoints at task boundaries. Never trust subagent success claims without independent evidence (re-run the affected suite, review the diff).
- One writer per worktree; never dispatch a second writer into a worktree while an earlier writer may be active. If writer state is UNKNOWN, terminate the prior session when possible and use a read-only investigator until exclusivity is re-established.
- Safe to parallelize: independent issues in separate worktrees, unrelated subsystems, read-only research/critique/verification. Never parallelize writers touching the same files, sharing mutable external state, depending on each other's output, or altering the same schema/API/migration sequence.

## Session-end reconciliation (mandatory — never leave stranded work)
- After every merge to main: `git pull --ff-only`, close the issue with a one-line receipt (squash SHA + gate status + tests + exact head), then delete the worktree (`git worktree remove --force` only when merged + clean + nothing unpushed + head in merged history + no active subagent), delete the remote claim branch, delete the local branch. Verify with `git worktree list --porcelain` and `git branch -a`.
- Sweep for leftovers every session end: merged branches (local + remote) deleted; stale claim branches for closed issues deleted; orphaned worktrees reported, never auto-deleted when ambiguous. `prune-safe` only removes conclusively safe worktrees.
- Handoff discipline: refresh the session handoff prompt/file before context runs low so the next session resumes exactly (live head SHA, open-issue order, in-progress heads, next commands). Keep one managed work-state comment per active issue (marker-based, updated in place, no local absolute paths) so any agent can read the issue and see where work stopped. No in-progress unit may remain stranded without a handoff pointer.

## Engineering standard
- Make the smallest correct change. Reuse existing code before adding abstractions, files, dependencies, or layers.
- Edit the canonical implementation in place. Do not create parallel `v2`, `new`, `fixed`, `final`, or temp implementations unless migration is explicit.
- Keep code lean: low complexity, focused files, clear names, no speculative generality.
- Innovate where it improves the user outcome; do not invent architecture without a concrete need.
- Do not hide failures with silent fallbacks, broad catches, fake success, skipped tests, or weakened assertions.
- For behavior changes, write or update tests first when practical. Prove the test can fail for the right reason.
- For bugs or unexpected behavior, find root cause before fixing.

## Verification
- Before claiming success, run the narrowest command that proves the claim. Read the output and report the command plus result.
- If verification cannot run, say why and give the exact next command.
- Do not call work complete while tests, checks, review, cleanup, or user acceptance remain unresolved.

## Secrets and authority
- Never ask the user to paste secrets into chat. Never print, commit, or log secret values.
- Local agent config source of truth: `~/.config/agents\`.
- Store secrets in the configured secret provider. Only the machine identity needed to read that provider may remain in local plaintext.
- When cleaning up scattered local secret/password files, first consolidate unique values into one owner-review file on the Desktop, then delete the scattered originals. Do not print those values in chat, logs, issues, PRs, or command output.
- Current provider adapters (for example, Infisical and GitHub) are implementation details in `~/.config/agents\README.md` and `config.yaml`; keep global rules vendor-neutral.
- For governed software-engineering work, Git/repo mutations must use the global `dev-agent` repo identity minted from the secret provider. The owner's interactive repo credential is break-glass/admin only; do not use it for routine agent issue, branch, PR, label, comment, or merge actions unless the owner explicitly says to use the owner credential.
- If the `dev-agent` repo token cannot be minted, continue only with read-only discovery and report the blocker. Do not silently fall back to owner credentials.
- Use the global `review-agent` repo identity for independent review comments/findings when a review agent is acting. The implementation names of current Apps are adapter details; the portable roles are `dev-agent` and `review-agent`.
- The active harness is the source of truth for the builder provider family and model. Select/record a reviewer provider family and model separately, and keep the reviewer provider family different from the builder's for material review. Re-check this whenever the harness provider/model changes.
- Record provider family, model, and repo identity in work-state comments, PR descriptions, review comments, issue evidence, and handoffs whenever an agent performs governed work.
- If blocked by permissions, hooks, or a needed secret, do not work around it. Use the documented guard/vault flow or ask for owner action.
- Guard/runbox workflow: write self-contained scripts to `~/code\guards\runbox\` when required; use `node C:/code/guards/hooks/engine.mjs vault-keys` / `apply` where documented.

## Git and delivery
- Do not push to protected branches unless explicitly instructed.
- Keep commits focused. Do not mix unrelated refactors with requested work.
- Preserve user work. Check status before risky edits. Do not hard-delete important local config, credentials, keys, vault paths, or procedures; archive first.

## Communication
- Be concise and specific.
- State facts with evidence. Do not say "should work."
- Ask only when blocked or when the choice changes outcome, risk, or scope.
