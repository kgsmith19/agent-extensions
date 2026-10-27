"""CLI for agent_extensions.install: bootstrap | bundle | update | init [repo] | status.

Exit code 0 iff the run's report is ok (no failed stages). status is
read-only. Its machine-stage lines are informational; its AGENT_* bundle
section is the drift and freshness gate (issue #61): exit 1 on any DRIFT or
MISSING key, 2 when the secret provider cannot be reached, with the converge
command printed. `status --repair` runs bootstrap's bundle stage, then
verifies again.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path


def _print_report(report) -> None:
    for s in report.stages:
        print(f"{s.status:8} {s.name}: {s.detail}")


def _tier_status_lines(install_repo: Path, home: Path) -> list[str]:
    """Read-only tiers/claude lines (issue #81), mirroring the shim states."""
    from agent_extensions import install as pkg
    from agent_extensions.install import tiers
    from agent_extensions.install.machine import render_template

    gate = home / ".claude"
    if not gate.exists():
        return ["skipped  tiers/claude: gate absent (~/.claude)"]
    try:
        models = tiers.claude_agent_models(tiers.resolve_tiers(home, os.environ))
    except tiers.TierConfigError as exc:
        return [f"drifted  tiers/claude: invalid tier config ({exc})"]
    lines: list[str] = []
    for role, template in tiers.CLAUDE_AGENT_TEMPLATES.items():
        t = gate / "agents" / f"{role}.md"
        if not t.exists():
            lines.append(f"drifted  tiers/claude: {role}.md missing")
            continue
        current = t.read_text(encoding="utf-8")
        if pkg.MANAGED_MD_MARKER not in current:
            lines.append(f"unmanaged tiers/claude: {role}.md present, not ours ({t})")
        elif current == render_template(install_repo, template).replace("{model}", models[role]):
            lines.append(f"applied  tiers/claude: {role}.md current (model {models[role]})")
        else:
            lines.append(f"drifted  tiers/claude: {role}.md differs from rendered tiers")
    return lines


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

    lines.extend(_tier_status_lines(install_repo, home))

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
        if os.name != "nt" and not os.access(launcher, os.X_OK):
            lines.append(f"drifted  cli-shim: {launcher} not executable (re-run bootstrap)")
        else:
            lines.append(f"applied  cli-shim: {launcher}")
    elif launcher.exists():
        lines.append(f"unmanaged cli-shim: {launcher}")
    else:
        lines.append(f"drifted  cli-shim: {launcher} missing")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="ae", description="agent-extensions installer (issue #53)")
    sub = parser.add_subparsers(dest="cmd", required=True)
    p_boot = sub.add_parser("bootstrap", help="install machine stages + capability bundle (idempotent)")
    p_boot.add_argument(
        "--seed",
        default=None,
        help="machine-identity seed file (INFISICAL_CLIENT_ID/SECRET/PROJECT_ID lines); "
        "materializes the AGENT_* instruction bundle (default: the INFISICAL_* environment, if set)",
    )
    p_bundle = sub.add_parser(
        "bundle", help="materialize only the AGENT_* instruction bundle (session start; idempotent)"
    )
    p_bundle.add_argument("--seed", default=None, help="machine-identity seed file (default: the INFISICAL_* environment)")
    sub.add_parser("update", help="ensure latest checkout, then converge (idempotent)")
    p_init = sub.add_parser("init", help="forceful repo-mode injection (gitignored)")
    p_init.add_argument("repo", nargs="?", default=None, help="target repo (default: cwd)")
    p_status = sub.add_parser(
        "status", help="read-only machine state report + AGENT_* bundle drift gate (exit 1 drift, 2 unreachable)"
    )
    p_status.add_argument("--seed", default=None, help="machine-identity seed file (default: the INFISICAL_* environment)")
    p_status.add_argument(
        "--repair", action="store_true", help="on DRIFT or MISSING, run bootstrap's bundle stage, then verify again"
    )
    p_queue = sub.add_parser("queue", help="the owner's execution queue: `brief`, or any std "
                             "work_queue.py command (exit code preserved)")
    p_queue.add_argument("args", nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    if args.cmd == "queue":
        from agent_extensions.queue_cli import main as queue_main

        return queue_main(args.args)

    home = Path(os.path.expanduser("~"))
    here = Path(__file__).resolve()
    install_repo = Path(
        os.environ.get("AGENT_EXTENSIONS_DIR")
        or next((p for p in here.parents if (p / "agent_extensions" / "install").exists()), home / ".agent-extensions")
    )

    if args.cmd == "status":
        from agent_extensions.install import bundle, bundle_status

        print(_probe_status(install_repo, home))
        seed = Path(args.seed).expanduser() if args.seed else None
        report = bundle_status.verify(home, seed_path=seed, env=os.environ)
        print("\n".join(report.lines()))
        if args.repair and report.exit_code == bundle_status.EXIT_DRIFT:
            result = bundle.stage_bundle(home, seed_path=seed, env=os.environ, client=bundle_status.default_client(os.environ))
            print(f"repair: {result.status} {result.name}: {result.detail}")
            report = bundle_status.verify(home, seed_path=seed, env=os.environ)
            print("\n".join(report.lines()))
        return report.exit_code
    if args.cmd == "bundle":
        from agent_extensions.install import bundle

        seed = Path(args.seed).expanduser() if args.seed else None
        result = bundle.stage_bundle(home, seed_path=seed, env=os.environ)
        print(f"{result.status:8} {result.name}: {result.detail}")
        return 1 if result.status == "failed" else 0
    if args.cmd == "update":
        from agent_extensions.install.update import cmd_update

        report = cmd_update(home=home, install_repo=install_repo)
    elif args.cmd == "bootstrap":
        from agent_extensions.install import machine

        seed = Path(args.seed).expanduser() if args.seed else None
        report = machine.bootstrap(install_repo, home=home, seed=seed)
    else:  # init — forceful repo-mode injection
        from agent_extensions.install.repo import init

        target = Path(args.repo) if args.repo else Path.cwd()
        report = init(target, install_repo=install_repo, home=home)
    _print_report(report)
    return 0 if report.ok() else 1


if __name__ == "__main__":
    sys.exit(main())
