"""Discover and run every bootstrap/*.test.sh or *.test.ps1 script.

Every script runs by default. The only exclusions are the live
integration scripts named in LIVE_ONLY, each with the reason it cannot run
hermetically; an entry whose file no longer exists, or that lacks a reason,
fails discovery so the list can never silently drift.

    python .github/ci/script_tests.py sh|ps1
"""

from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

LIVE_ONLY = {
    "bootstrap/sync.claude-code.test.ps1": (
        "drives the real, authenticated `claude` CLI and registers plugins in "
        "the machine's real Claude config (not scratch-isolated by design)"
    ),
}

RUNNERS = {
    "sh": ["bash"],
    "ps1": ["pwsh", "-NoProfile", "-NonInteractive", "-File"],
}


class StaleExclusion(Exception):
    """A LIVE_ONLY entry names a missing file or gives no reason."""


@dataclass
class Result:
    name: str
    code: int
    output: str


def discover(root, kind, live_only=None):
    live_only = LIVE_ONLY if live_only is None else live_only
    for rel, reason in live_only.items():
        if not reason.strip():
            raise StaleExclusion("%s is excluded without a reason" % rel)
        if rel.endswith(".test." + kind) and not (Path(root) / rel).is_file():
            raise StaleExclusion("%s is excluded but does not exist" % rel)
    run, excluded = [], []
    for path in sorted((Path(root) / "bootstrap").glob("*.test." + kind)):
        rel = path.relative_to(root).as_posix()
        if rel in live_only:
            excluded.append((rel, live_only[rel]))
        else:
            run.append(path)
    return run, excluded


def run_all(root, kind, live_only=None):
    run, excluded = discover(root, kind, live_only)
    for rel, reason in excluded:
        print("EXCLUDED (live-only) %s: %s" % (rel, reason))
    results = []
    for path in run:
        proc = subprocess.run(RUNNERS[kind] + [str(path)], cwd=root,
                              capture_output=True, text=True, timeout=600)
        output = proc.stdout + proc.stderr
        results.append(Result(path.name, proc.returncode, output))
        print("%s %s" % ("PASS" if proc.returncode == 0 else "FAIL", path.name))
        if proc.returncode != 0:
            print(output)
    return results


def summarize(results):
    failed = [r.name for r in results if r.code != 0]
    print("%d run, %d failed%s" % (len(results), len(failed),
                                   (": " + ", ".join(failed)) if failed else ""))
    if not results:
        print("ERROR: discovered zero scripts")
        return 1
    return 1 if failed else 0


def main(argv):
    if len(argv) != 1 or argv[0] not in RUNNERS:
        print(__doc__)
        return 2
    return summarize(run_all(ROOT, argv[0]))


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
