"""CLI for the provider-neutral continuity capsules.

    python -m agent_extensions.continuity capture [--task ...] [--next ...] [--auto]
    python -m agent_extensions.continuity render  [--format plain|claude]
    python -m agent_extensions.continuity status  [--json]
    python -m agent_extensions.continuity clear

Harness adapters call ``render`` on session start and ``capture --auto``
before compaction/shutdown. Nothing here is provider-specific.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import List, Optional

from agent_extensions.continuity.capsule import (
    Capsule,
    capsule_path,
    refresh,
)


def _add_common(p: argparse.ArgumentParser) -> None:
    p.add_argument("--cwd", default=".", help="project directory (default: cwd)")
    p.add_argument("--dir", default=None, help="capsule store dir override")


def _extend_unique(existing: List[str], new: List[str]) -> List[str]:
    out = list(existing)
    for item in new:
        if item not in out:
            out.append(item)
    return out


def _cmd_capture(args: argparse.Namespace) -> int:
    cwd = Path(args.cwd).resolve()
    directory = Path(args.dir) if args.dir else None
    cap = refresh(cwd, harness=args.harness or "", model=args.model or "", directory=directory)

    if not args.auto:
        if args.task is not None:
            cap.task = args.task
        if args.phase is not None:
            cap.phase = args.phase
        if args.next is not None:
            cap.next_action = args.next
        cap.blockers = _extend_unique(cap.blockers, args.blocker or [])
        cap.decisions = _extend_unique(cap.decisions, args.decision or [])
        cap.evidence = _extend_unique(cap.evidence, args.evidence or [])
        cap.open_issues = sorted(set(cap.open_issues) | set(args.issue or []))

    path = cap.save(capsule_path(cwd, directory))
    print(path)
    return 0


def _cmd_render(args: argparse.Namespace) -> int:
    cwd = Path(args.cwd).resolve()
    directory = Path(args.dir) if args.dir else None
    cap = Capsule.load(capsule_path(cwd, directory))
    if args.refresh:
        cap = refresh(cwd, harness=args.harness or "", model=args.model or "", directory=directory)
    text = cap.render(args.format)
    if text:
        sys.stdout.write(text + "\n")
    return 0


def _cmd_status(args: argparse.Namespace) -> int:
    cwd = Path(args.cwd).resolve()
    directory = Path(args.dir) if args.dir else None
    cap = Capsule.load(capsule_path(cwd, directory))
    if args.json:
        print(json.dumps(cap.__dict__, indent=2, sort_keys=True))
    else:
        print(f"capsule: {capsule_path(cwd, directory)}")
        print(f"empty:   {cap.is_empty()}")
    return 0


def _cmd_clear(args: argparse.Namespace) -> int:
    cwd = Path(args.cwd).resolve()
    directory = Path(args.dir) if args.dir else None
    path = capsule_path(cwd, directory)
    if path.exists():
        path.unlink()
        print(f"removed {path}")
    else:
        print(f"no capsule at {path}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="agent-capsule", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    cap = sub.add_parser("capture", help="write/refresh the capsule for a project")
    _add_common(cap)
    cap.add_argument("--task")
    cap.add_argument("--phase")
    cap.add_argument("--next", dest="next")
    cap.add_argument("--blocker", action="append")
    cap.add_argument("--decision", action="append")
    cap.add_argument("--evidence", action="append")
    cap.add_argument("--issue", type=int, action="append")
    cap.add_argument("--harness")
    cap.add_argument("--model")
    cap.add_argument("--auto", action="store_true", help="refresh auto fields only")
    cap.set_defaults(func=_cmd_capture)

    ren = sub.add_parser("render", help="print the capsule for injection")
    _add_common(ren)
    ren.add_argument("--format", choices=["plain", "claude"], default="plain")
    ren.add_argument("--refresh", action="store_true")
    ren.add_argument("--harness")
    ren.add_argument("--model")
    ren.set_defaults(func=_cmd_render)

    st = sub.add_parser("status", help="show the capsule path/state")
    _add_common(st)
    st.add_argument("--json", action="store_true")
    st.set_defaults(func=_cmd_status)

    cl = sub.add_parser("clear", help="delete the capsule")
    _add_common(cl)
    cl.set_defaults(func=_cmd_clear)

    return parser


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
