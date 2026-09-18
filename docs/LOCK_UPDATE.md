# extensions.lock — Update / Rollback Procedure (Stage 17)

## What this file is

`extensions.lock` is the reproducibility ground truth for every rendered
capability. Every entry pins an exact full 40-hex upstream commit, a valid
SPDX license, and a `sha256:<hex>` rendered digest. Verification is
fail-closed: `verify_lockfile()` returns problems; empty means sound.

## Live vs pinned (deliberate distinction)

- **Codex / Antigravity** consume only `upstream_kind: pinned` entries —
  exact commits recorded here. This is the reproducible path.
- **Claude Code** additionally tracks the live marketplace by name
  (`marketplace.agent-extensions-live`, `upstream_kind: live-marketplace`).
  That pointer is a name, not a reproducible lock, and is never used as a
  source of truth for renders.

## Updating a lock (deliberate, diffed, canaried)

1. Change the upstream source (new commit, new digest) — never hand-edit
   `source_commit` to a branch, tag-in-motion, or abbreviated SHA.
2. Rebuild: `build_lock_from_repo('.')` → `write_lockfile(lock, 'extensions.lock')`.
3. Append a history line: `update <identity> <old-sha> -> <new-sha> (<reason>)`.
4. Verify: `verify_lockfile(read_lockfile('extensions.lock')) == []`.
5. Run the full suite: `python -m pytest tests/ -q` — all green before PR.
6. Open a ready PR with the lock diff attached; a second pair of eyes
   confirms the upstream commit exists and the digest matches.

## Rollback

1. `git log --oneline -- extensions.lock` — find the last good commit.
2. `git show <good-sha>:extensions.lock > extensions.lock` — restore.
3. Verify (`verify_lockfile` + full suite) and PR the restoration with a
   history line: `rollback <identity> to <good-sha> (<reason>)`.
4. Never force-push the lockfile; rollback is a forward commit.

## Regenerating from scratch

Delete nothing by hand. `build_lock_from_repo('.')` re-reads every source
live (VENDORED-FROM pins, `bootstrap/external-marketplaces.json` pins and
resolvedCommits, skill content digests) and produces a fresh lockfile.
Diff it against the committed one — any unexpected change is a signal,
not noise.
