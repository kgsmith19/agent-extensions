"""ae update: ensure the agent-extensions checkout is present and current,
then converge the machine to it (issue #53).

Never stashes, resets, or discards local state: a dirty or diverged checkout
is a failed stage naming the exact resolution commands.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from agent_extensions.sync.bootstrap import APPLIED, BootstrapReport, StageResult, _run_stage
from agent_extensions.install.machine import bootstrap as machine_bootstrap

DEFAULT_REPO_URL = "https://github.com/kgsmith19/agent-extensions.git"


def _git(*args: str, cwd: Path | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], capture_output=True, text=True, cwd=cwd, timeout=300)


def resolve_install_dir(env: dict | None = None, home: Path | None = None) -> Path:
    """AGENT_EXTENSIONS_DIR wins; default is <home>/.agent-extensions."""
    e = env if env is not None else os.environ
    raw = e.get("AGENT_EXTENSIONS_DIR")
    if raw:
        return Path(raw).expanduser()
    h = Path(home) if home is not None else Path(os.path.expanduser("~"))
    return h / ".agent-extensions"


def ensure_checkout(install_dir: Path, repo_url: str | None = None) -> StageResult:
    """Clone when absent; fast-forward when present; never discard anything."""

    def work() -> str:
        if (install_dir / ".git").exists():
            status = _git("status", "--porcelain", cwd=install_dir)
            if status.returncode != 0:
                raise RuntimeError(f"git status failed in {install_dir}: {status.stderr.strip()[:200]}")
            if status.stdout.strip():
                raise RuntimeError(
                    f"checkout dirty — resolve then re-run: "
                    f"git -C {install_dir} status --porcelain (stash or commit; the updater never discards)"
                )
            pull = _git("pull", "--ff-only", cwd=install_dir)
            if pull.returncode != 0:
                raise RuntimeError(
                    f"fast-forward failed — resolve then re-run: git -C {install_dir} pull --ff-only "
                    f"({pull.stderr.strip()[:200]})"
                )
            return f"fast-forwarded {install_dir}"
        url = repo_url or os.environ.get("AGENT_EXTENSIONS_REPO_URL") or DEFAULT_REPO_URL
        clone = _git("clone", "--depth", "1", url, str(install_dir))
        if clone.returncode != 0:
            raise RuntimeError(f"clone failed: {clone.stderr.strip()[:300]}")
        return f"cloned {url} -> {install_dir}"

    return _run_stage("update-checkout", work, enabled=True)


def cmd_update(home: Path | None = None, env: dict | None = None, install_repo: Path | None = None) -> BootstrapReport:
    """ae update = ensure latest checkout, then run bootstrap stages to converge."""
    report = BootstrapReport()
    target = install_repo if install_repo is not None else resolve_install_dir(env=env, home=home)
    checkout = ensure_checkout(target)
    report.stages.append(checkout)
    if checkout.status == APPLIED:
        h = Path(home) if home is not None else Path(os.path.expanduser("~"))
        report.stages.extend(machine_bootstrap(target, home=h, env=env).stages)
    return report
