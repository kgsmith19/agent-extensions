"""The owner's execution queue from any harness (#72; std#283 phase 3).

std's queue CLI (`tools/work_queue.py` plus its sibling modules) owns
every queue rule. This module only finds that CLI and runs it, so every
harness and the `ae` launcher share one path:

    python3 -I agent_extensions/queue_cli.py brief      # session start
    python3 -I agent_extensions/queue_cli.py <args...>  # forwarded verbatim
    ae queue <args...>                              # same, via the launcher

The CLI is the local standard checkout (`$AES_STANDARD_DIR/tools`) when
present, otherwise every module in `QUEUE_MODULES` fetched once per run
from `$AE_QUEUE_BASE_URL` (default: std `main`) into a temp dir. A
partial fetch never runs. `brief` prints the active item, the next item
and the handoff; a failed section says `degraded` on its own line, and
the exit code is non-zero only when `next` itself fails. Forwarding keeps
std's arguments, streams and exit code; an unreachable std exits 5,
std's transport code. Standard library only: it runs under `python3 -I`.
"""

from __future__ import annotations

import contextlib
import os
import re
import subprocess
import sys
import tempfile
import urllib.request

QUEUE_BASE_URL = "https://raw.githubusercontent.com/kgsmith19/agent-engineering-standard/main/tools"
QUEUE_MODULES = ("queue_core.py", "queue_store.py", "work_queue.py")
EXIT_TRANSPORT = 5
_ACTIVE = re.compile(r"^Q\d+ \[active\] ")


class QueueUnavailable(Exception):
    """std's queue CLI could not be located or fetched in full."""


@contextlib.contextmanager
def queue_cli(env=None):
    """Yield the path of a runnable std work_queue.py."""
    env = os.environ if env is None else env
    local = os.path.join(env.get("AES_STANDARD_DIR") or "", "tools", "work_queue.py")
    if env.get("AES_STANDARD_DIR") and os.path.isfile(local):
        yield local
        return
    base = (env.get("AE_QUEUE_BASE_URL") or QUEUE_BASE_URL).rstrip("/")
    with tempfile.TemporaryDirectory(prefix="ae-queue-") as tmp:
        for module in QUEUE_MODULES:
            try:
                with urllib.request.urlopen("%s/%s" % (base, module), timeout=20) as response:
                    data = response.read()
            except (OSError, ValueError) as exc:
                raise QueueUnavailable("could not fetch %s from %s: %s" % (module, base, exc))
            with open(os.path.join(tmp, module), "wb") as handle:
                handle.write(data)
        yield os.path.join(tmp, "work_queue.py")


def _run(cli, args):
    return subprocess.run([sys.executable, "-I", cli, *args], capture_output=True,
                          text=True, timeout=120)


def _last_line(result):
    lines = (result.stderr or result.stdout or "").strip().splitlines()
    return lines[-1] if lines else "no output"


def _active_lines(show_output):
    """The active items from `show`, each with its indented detail lines."""
    picked, keep = [], False
    for line in show_output.splitlines():
        if not line.startswith(" "):
            keep = bool(_ACTIVE.match(line))
        if keep:
            picked.append(line)
    return picked


def brief(env=None, out=sys.stdout):
    """Print where the session is in the queue; return std's `next` code."""
    try:
        with queue_cli(env) as cli:
            nxt = _run(cli, ["next"])
            if nxt.returncode not in (0, 3):
                out.write(nxt.stdout)
                out.write(_last_line(nxt) + "\n")
                return nxt.returncode
            show = _run(cli, ["show"])
            handoff = _run(cli, ["handoff", "show"])
    except QueueUnavailable as exc:
        out.write("%s\n" % exc)
        return EXIT_TRANSPORT
    if show.returncode != 0:
        out.write("Active item: degraded (exit %d: %s)\n" % (show.returncode, _last_line(show)))
    else:
        active = _active_lines(show.stdout)
        out.write("Active item:%s\n" % ("" if active else " none"))
        for line in active:
            out.write(line + "\n")
    out.write("Next item:\n" + nxt.stdout)
    if handoff.returncode == 3:
        out.write("Handoff: none\n")
    elif handoff.returncode != 0:
        out.write("Handoff: degraded (exit %d: %s)\n" % (handoff.returncode, _last_line(handoff)))
    else:
        out.write("Handoff:\n" + handoff.stdout)
        if not handoff.stdout.endswith("\n"):
            out.write("\n")
    return nxt.returncode


def forward(args, env=None):
    """Run std's CLI with ``args``; its streams and exit code pass through."""
    try:
        with queue_cli(env) as cli:
            return subprocess.call([sys.executable, "-I", cli, *args])
    except QueueUnavailable as exc:
        sys.stderr.write("ae queue: %s\n" % exc)
        return EXIT_TRANSPORT


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv == ["brief"]:
        return brief()
    return forward(argv)


if __name__ == "__main__":
    sys.exit(main())
