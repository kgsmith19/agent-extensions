# Resume prompt — agent-extensions completion milestone

Paste the block below as the first message of a new Claude Code session.
Delete this file once the milestone is done.

---

Resume the agent-extensions completion milestone. Do not re-plan or
re-brainstorm anything — the design is written, committed, and approved at
`docs/superpowers/specs/2026-08-27-completion-milestone-design.md`. Read
state from files and `git log`, not from me.

**Where things stand (corrected 2026-08-27 — the previous version of this
file was stale and self-contradictory; verified directly against
`origin/main` and `git log`, not trusted from a prior session's self-report):**

- **Spec 1 (full-plugin support) is done, merged, and pushed.** `main`,
  `origin/main`, and any freshly-checked-out branch are at `343eeeb Fix
  external marketplace MCP normalization`, with merge commit `1564a7e Merge
  worktree-full-plugin-support into main` in that history. Tasks 1–17 are
  all committed. The previous version of this file instructed discarding
  and re-dispatching Tasks 13–17 as "unsafe" — that was true when written,
  but is obsolete: those tasks were completed cleanly and merged afterward.
  Do not redo them.
- **Specs 2–5 are in progress** on branch
  `claude/agent-extensions-plugin-support-nuahzf`, executed in strict
  dependency order per the milestone design (Spec 2 defines the bootstrap
  stage interface that Specs 3 and 4 each register against; Spec 5 closes
  the milestone). Check `git log` on that branch and the PR for kgsmith19/agent-extensions
  for exactly how far execution got — this file is not the source of truth
  for that, the commits are.

**Two facts worth not rediscovering:**

- No `pwsh` is available in a standard bash-only Linux container, so the
  `.ps1` scripts can only be verified by careful reading/mirroring there,
  not live execution. Live-verify `.ps1` changes on a Windows or
  PowerShell-capable machine before trusting them.
- PowerShell gotcha, already bitten this codebase twice: a function's
  uncaptured pipeline output joins its return value when the caller
  assigns it. Use `Write-Host`, never `Write-Output`, for status messages
  inside value-returning `.ps1` functions.

**If resuming after an interruption:** the milestone design's own
sequencing rule is load-bearing, not a suggestion — Spec 1's Tasks 1-11
are the cautionary tale for what happens when code is built on an
unverified assumption (exited 0 while silently linking 0 of 17 skills).
Don't build Spec 3 or 4 registrations against a Spec 2 stage interface
that isn't actually merged and live-verified yet.
