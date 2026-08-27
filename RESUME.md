# Resume prompt — agent-extensions completion milestone

Paste the block below as the first message of a new Claude Code session
started in `C:\code\agent-extensions`. Delete this file once the milestone
is done.

---

Resume the agent-extensions completion milestone. Do not re-plan or
re-brainstorm anything — the design and plan are written, committed, and
approved. Read state from files, not from me.

**Read these first, in this order:**

1. `docs/superpowers/specs/2026-08-27-completion-milestone-design.md` — the
   five-spec roadmap for the whole milestone (on `main`).
2. `.claude/worktrees/full-plugin-support/.superpowers/sdd/2026-08-20-full-plugin-support/progress.md`
   — the SDD ledger. This is the authority on what is done. Trust it and
   `git log` over anything you or I remember.
3. `docs/superpowers/specs/2026-08-20-full-plugin-support-design.md` — read
   the `## Amendment, 2026-08-27` section at the end; it supersedes parts of
   what precedes it.

**Where things stand.**

Spec 1 of 5 is in progress in the worktree
`C:\code\agent-extensions\.claude\worktrees\full-plugin-support`
(branch `worktree-full-plugin-support`, HEAD `73261f5`).

Tasks 1–11 were implemented in a prior session, then live verification found
them broken: `sync.ps1` printed `Sync complete.` and exited 0 while linking 0
of 17 external skills, leaving `~/.codex/config.toml` byte-identical and
creating 39 Antigravity junctions pointing at empty directories. Root cause:
plugin location was inferred as `<cache>/<marketplace>/<plugin>`, but the
marketplace's `.claude-plugin/marketplace.json` declares it — 22 plugins
inline (across `plugins/` and `external_plugins/`), 17 as separate git repos.
Every miss was silent. All nine test suites passed because the fixture shared
the code's wrong assumption.

Tasks 12–17 fix that. Task 12 is complete and reviewed (`73261f5`).

**Task 13 is in an unsafe state.** Its implementer was still running when the
session ended and left uncommitted work: modified `bootstrap/sync.ps1`, plus
untracked `bootstrap/plugin-source.test.ps1` and `bootstrap/plugin-source.test.sh`.
No test run was ever observed and no review ran, so none of it is trusted.
The safer default is to discard and re-dispatch clean:

    cd C:\code\agent-extensions\.claude\worktrees\full-plugin-support
    git checkout -- bootstrap/sync.ps1
    rm -f bootstrap/plugin-source.test.ps1 bootstrap/plugin-source.test.sh

**Then continue.** Use `superpowers:subagent-driven-development` to execute
Tasks 13–17 of `docs/superpowers/plans/2026-08-20-full-plugin-support.md`.
Briefs for Tasks 12–17 are already generated in the SDD workspace; regenerate
any you need with the skill's `scripts/task-brief`.

**Two rulings I already made — carry them forward, don't re-ask:**

1. Tasks 12–15 deliberately leave exactly three suites RED until Task 16
   lands: `vendor-cache`, `sync-external-codex`, `sync-external-antigravity`
   (both `.ps1` and `.sh`). The plan governs. Tell every reviewer the
   expected-red list, and have it verify that no assertion inside those
   suites was weakened, deleted, or skipped — that is the thing the
   exemption could otherwise hide. A fourth red suite is a real finding.
2. Bash test mirrors are specified by the accessor mapping table in the
   plan's amendment header rather than written out in full. That is
   sufficient; reviewers check assertion-for-assertion parity with the
   PowerShell suite.

**Environment facts worth not rediscovering:**

- This machine cannot create real symlinks — `ln -s` exits non-zero and
  yields a plain directory. No WSL, no Docker, not admin. Bash suites whose
  assertions need a live link fail for that reason alone; it is a known
  environment limit, not a defect. PowerShell junctions work fine.
- `jq` 1.8.2 is on PATH, and this Windows build emits CRLF. Strip `\r` when
  comparing jq output to an expected string, or comparisons fail for reasons
  unrelated to the code.
- PowerShell: a function's uncaptured pipeline output joins its return value
  when the caller assigns it. Use `Write-Host`, never `Write-Output`, for
  status messages inside value-returning functions. This bug has bitten this
  codebase twice.
- Config backups from the live verification run are at
  `~/.agent-extensions-verify-backup-1787840201`.

**After Spec 1 lands:** use `superpowers:finishing-a-development-branch`,
then Specs 2–5 from the milestone design each need their own brainstorm →
design → plan → worktree. Spec 2 must land before 3 and 4, because it defines
the bootstrap stage interface they both register against.

**Also outstanding:** `main` has 5 unpushed commits, and the GitHub repo
`kgsmith19/agent-extensions` is still private. Spec 2 covers making it public
(gated on a git-history secret scan, not a working-tree scan).
