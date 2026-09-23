"""CLI for agent_extensions.install: bootstrap | update | init [repo] | status.

Exit code 0 iff the run's report is ok (no failed stages). status is
informational and read-only; it never exits non-zero for drift.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path


def _print_report(report) -> None:
    for s in report.stages:
        print(f"{s.status:8} {s.name}: {s.detail}")


def _probe_status(install_repo: Path, home: Path) -> str:
    """Read-only state lines (never writes): one line per machine stage."""
    from agent_extensions import install as pkg
    from agent_extensions.install.machine import (
        MANAGED_SH_MARKER,
        SHIMS,
        render_template,
        _excludes_target,
    )

    lines: list[str] = []

    target = _excludes_target(home)
    if target is None or not Path(target).exists():
        lines.append("skipped  gitignore: no excludes file")
    else:
        have = Path(target).read_text(encoding="utf-8").splitlines()
        missing = [e for e in pkg.GITIGNORE_ENTRIES if e not in have]
        lines.append(
            f"{'applied' if not missing else 'drifted'}  gitignore: {target}"
            + (f" (missing: {', '.join(missing)})" if missing else "")
        )

    for spec in SHIMS:
        gate = spec.gate(home)
        t = spec.target(home)
        if gate is not None and not gate.exists():
            lines.append(f"skipped  shims/{spec.name}: gate absent")
        elif not t.exists():
            lines.append(f"drifted  shims/{spec.name}: missing {t}")
        elif pkg.MANAGED_MD_MARKER not in t.read_text(encoding="utf-8"):
            lines.append(f"unmanaged shims/{spec.name}: {t} (present, not ours)")
        elif t.read_text(encoding="utf-8") == render_template(install_repo, spec.template):
            lines.append(f"applied  shims/{spec.name}: current")
        else:
            lines.append(f"drifted  shims/{spec.name}: differs from template")

    hooks = home / ".claude" / "settings.json"
    if hooks.exists() and pkg.MANAGED_MD_MARKER or hooks.exists():
        txt = hooks.read_text(encoding="utf-8")
        wired = "claude_hook.py" in txt
        lines.append(f"{'applied' if wired else 'drifted'}  hooks/claude: {'wired' if wired else 'not wired'}")
    else:
        lines.append("drifted  hooks/claude: settings.json missing")
    pi = home / ".pi" / "agent" / "settings.json"
    if pi.exists() and "pi-extension.ts" in pi.read_text(encoding="utf-8"):
        lines.append("applied  hooks/pi: wired")
    else:
        lines.append("drifted  hooks/pi: not wired")

    name = os.environ.get("AE_COMMAND", "ae")
    launcher = home / "bin" / name
    if launcher.exists() and MANAGED_SH_MARKER in launcher.read_text(encoding="utf-8"):
        lines.append(f"applied  cli-shim: {launcher}")
    elif launcher.exists():
        lines.append(f"unmanaged cli-shim: {launcher}")
    else:
        lines.append(f"drifted  cli-shim: {launcher} missing")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="ae", description="agent-extensions installer (issue #53)")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("bootstrap", help="install machine stages + capability bundle (idempotent)")
    sub.add_parser("update", help="ensure latest checkout, then converge (idempotent)")
    p_init = sub.add_parser("init", help="forceful repo-mode injection (gitignored)")
    p_init.add_argument("repo", nargs="?", default=None, help="target repo (default: cwd)")
    sub.add_parser("status", help="read-only machine state report")
    args = parser.parse_args(argv)

    home = Path(os.path.expanduser("~"))
    install_repo = Path(
        os.environ.get("AGENT_EXTENSIONS_DIR")
        or (Path(__file__).resolve().parents[3] if (Path(__file__).resolve().parents[3] / "agent_extensions").exists() else home / ".agent-extensions")
    )

    if args.cmd == "status":
        print(_probe_status(install_repo, home))
        return 0
    if args.cmd == "update":
        from agent_extensions.install.update import cmd_update

        report = cmd_update(home=home, install_repo=install_repo)
    elif args.cmd == "bootstrap":
        from agent_extensions.install.machine import bootstrap

        report = bootstrap(install_repo, home=home)
    else:  # init — implemented in Task 5
        from agent_extensions.sync.bootstrap import BootstrapReport, StageResult

        report = BootstrapReport(
            stages=[StageResult(name="init", status="failed", detail="not implemented yet (Task 5)")]
        )
    _print_report(report)
    return 0 if report.ok() else 1


if __name__ == "__main__":
    sys.exit(main())
