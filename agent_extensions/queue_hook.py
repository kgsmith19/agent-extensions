"""Claim guard as a Claude Code PreToolUse hook (#77; std#287 AC4).

The claim protocol's atomic step is creating the Issue's
`issue/<n>-<slug>` branch, through MCP `create_branch` or
`gh api repos/O/R/git/refs`. This hook recognises that step in the
PreToolUse payload on stdin and runs std's
`work_queue.py guard --claim OWNER/REPO#N` (located and fetched by
queue_cli). Exit codes follow the Claude Code hook contract:

    0  allow: not a claim, the Issue is on the queue, or it was not and
       the hook queued it at the top with std's `claim` (std #331: the
       queue is the plan, not a lock; adding work is one step)
    2  block: the queue could not be read or written (fail closed); the
       reason goes to stderr, which the agent sees

Non-claim calls return at once without any network call. Registered by
`ae bootstrap` (install.machine.stage_hooks) in ~/.claude/settings.json.
Standard library only.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import queue_cli  # noqa: E402

BLOCK = 2
_BRANCH = re.compile(r"^issue/([1-9][0-9]*)-")
_GH_REFS = re.compile(r"\bgh\s+api\b.*?\brepos/([A-Za-z0-9][\w.-]*)/([\w.-]+)/git/refs(?:\s|$)")
_NEW_REF = re.compile(r"refs/heads/issue/([1-9][0-9]*)-")


def claim_ref(payload):
    """The Issue a tool call claims, as OWNER/REPO#N, or None."""
    if not isinstance(payload, dict):
        return None
    tool = payload.get("tool_name")
    args = payload.get("tool_input") or {}
    if not isinstance(args, dict):
        return None
    if tool == "mcp__github__create_branch":
        match = _BRANCH.match(str(args.get("branch") or ""))
        if match and args.get("owner") and args.get("repo"):
            return "%s/%s#%s" % (args["owner"], args["repo"], match.group(1))
    elif tool == "Bash":
        command = str(args.get("command") or "")
        repo, ref = _GH_REFS.search(command), _NEW_REF.search(command)
        if repo and ref:
            return "%s/%s#%s" % (repo.group(1), repo.group(2), ref.group(1))
    return None


def main(stdin=sys.stdin):
    try:
        payload = json.loads(stdin.read() or "null")
    except ValueError:
        return 0  # not a payload we can read as a claim; never wedge the harness
    ref = claim_ref(payload)
    if ref is None:
        return 0
    try:
        with queue_cli.queue_cli() as cli:
            result = subprocess.run([sys.executable, "-I", cli, "guard", "--claim", ref],
                                    capture_output=True, text=True, timeout=120)
    except queue_cli.QueueUnavailable as exc:
        sys.stderr.write("queue guard: claim of %s blocked, the execution queue could not be "
                         "read (%s). Fix access and retry; claims are never allowed blind.\n"
                         % (ref, exc))
        return BLOCK
    if result.returncode == 0:
        return 0
    reason = (result.stderr or result.stdout).strip() or "exit %d" % result.returncode
    if result.returncode == 1:
        # Refused only because the Issue is not queued: queue it, then allow.
        try:
            with queue_cli.queue_cli() as cli:
                added = subprocess.run([sys.executable, "-I", cli, "claim", ref],
                                       capture_output=True, text=True, timeout=120)
        except queue_cli.QueueUnavailable as exc:
            added = None
            reason += " (and the queue could not be fetched to add it: %s)" % exc
        if added is not None and added.returncode == 0:
            print("queue guard: %s was not queued; added it at the top (%s)"
                  % (ref, (added.stdout or "").strip().replace("\n", "; ")))
            return 0
        if added is not None:
            reason += " (and adding it failed: %s)" % (
                (added.stderr or added.stdout).strip() or "exit %d" % added.returncode)
    sys.stderr.write("queue guard: claim of %s blocked. %s\n" % (ref, reason))
    return BLOCK


if __name__ == "__main__":
    sys.exit(main())
