"""Forceful repo-mode injection (issue #53): overlay, bindings, guards.

Everything lands in gitignored paths by construction (the machine gitignore
stage installs the excludes entries). Additive only: the repo's committed
files are never touched; files we manage carry the marker and converge;
files without it are skipped loudly, never clobbered.
"""

from __future__ import annotations

import os
from pathlib import Path

from agent_extensions import install
from agent_extensions.install import machine as machine_mod
from agent_extensions.sync.bootstrap import FAILED, SKIPPED, BootstrapReport, StageResult, _run_stage

OVERLAY_LIMIT = 32 * 1024


def _overlay_text(install_repo: Path, repo: Path) -> str:
    """Template + optional gitignored AGENTS.local.d/*.md fragments, bounded."""
    base = machine_mod.render_template(install_repo, "AGENTS.local.md")
    parts = [base]
    frag_dir = repo / "AGENTS.local.d"
    if frag_dir.is_dir():
        for frag in sorted(frag_dir.glob("*.md")):
            try:
                text = frag.read_text(encoding="utf-8", errors="replace")
            except OSError as exc:
                raise RuntimeError(f"unreadable fragment {frag}: {exc}") from exc
            parts.append(f"\n## fragment: {frag.name}\n\n{text.strip()}\n")
    content = "\n".join(parts)
    if len(content.encode("utf-8")) > OVERLAY_LIMIT:
        raise RuntimeError(
            f"rendered overlay exceeds {OVERLAY_LIMIT} bytes ({len(content.encode('utf-8'))}) — "
            "trim AGENTS.local.d fragments"
        )
    return content


def stage_overlay(repo: Path, install_repo: Path, home: Path) -> StageResult:
    """Render the gitignored AGENTS.local.md overlay (baseline rules life)."""

    def work() -> str:
        target = repo / "AGENTS.local.md"
        content = _overlay_text(install_repo, repo)
        if target.exists():
            current = target.read_text(encoding="utf-8")
            if install.MANAGED_MD_MARKER not in current:
                return f"skipped (unmanaged file present: {target})"
            if current == content:
                return "up to date"
            target.write_text(content, encoding="utf-8", newline="\n")
            return "updated"
        target.write_text(content, encoding="utf-8", newline="\n")
        return "installed"

    return _run_stage("overlay", work, enabled=True)


def stage_bindings(repo: Path, install_repo: Path, home: Path, backup_root: Path | None = None) -> StageResult:
    """Claude settings.local.json (capsule hooks + PreToolUse guard) and the
    pi project binding (.pi/settings.json — only when absent per ruling R1)."""

    def work() -> str:
        notes: list[str] = []
        claude_cmd = "python \"" + (Path(install_repo) / machine_mod.CLAUDE_HOOK_REL).as_posix() + "\""
        guard_cmd = "python \"" + (Path(install_repo) / "agent_extensions" / "install" / "guards.py").as_posix() + "\""
        target = repo / ".claude" / "settings.local.json"

        current_text = target.read_text(encoding="utf-8") if target.exists() else None
        data = machine_mod._read_json(target) if current_text is not None else {}
        changed = False
        hooks = data.get("hooks")
        if not isinstance(hooks, dict):
            hooks = {}
            data["hooks"] = hooks
            changed = True
        wants = {"SessionStart": claude_cmd, "PreCompact": claude_cmd, "PreToolUse": guard_cmd}
        for event, cmd in wants.items():
            entries = hooks.get(event)
            if not isinstance(entries, list):
                hooks[event] = []
                changed = True
            if not any(
                isinstance(h, dict) and cmd in str(h.get("command", ""))
                for e in hooks[event]
                for h in (e.get("hooks") or [])
            ):
                hooks[event].append(machine_mod._hook_entry(cmd))
                changed = True
        if changed:
            if current_text is not None and backup_root is not None:
                machine_mod.backup(target, backup_root)
            machine_mod._write_json(target, data)
            notes.append("claude: merged")
        else:
            notes.append("claude: already current")

        pi_path = repo / ".pi" / "settings.json"
        if pi_path.exists():
            notes.append("pi: skipped (project .pi/settings.json already exists — team-owned)")
        else:
            ext_abs = (Path(install_repo) / machine_mod.PI_EXTENSION_REL).resolve().as_posix()
            machine_mod._write_json(pi_path, {"extensions": [ext_abs]})
            notes.append("pi: bound")
        return "; ".join(notes)

    return _run_stage("bindings", work, enabled=True)


def stage_guards(repo: Path, install_repo: Path, home: Path) -> StageResult:
    """Prove the compliance guard is runnable from the checkout (ruling R2:
    the guard versions with the installer; nothing extra lands in the repo)."""

    def work() -> str:
        import subprocess
        import sys

        guard = Path(install_repo) / "agent_extensions" / "install" / "guards.py"
        result = subprocess.run(
            [sys.executable, str(guard), "--selfcheck"], capture_output=True, text=True, timeout=60
        )
        if result.returncode != 0:
            raise RuntimeError(f"guard selfcheck failed: {result.stderr.strip()[:200]}")
        return "guard selfcheck ok"

    return _run_stage("guards", work, enabled=True)


def stage_status_clean(repo: Path, home: Path) -> StageResult:
    """Prove injection is invisible: git status --porcelain must be empty."""

    def work() -> str:
        import subprocess
        result = subprocess.run(
            ["git", "-C", str(repo), "status", "--porcelain"], capture_output=True, text=True, timeout=60
        )
        if result.returncode != 0:
            raise RuntimeError(f"git status failed: {result.stderr.strip()[:200]}")
        if result.stdout.strip():
            raise RuntimeError(f"repo not clean after injection:\n{result.stdout.strip()[:500]}")
        return "git status clean"

    return _run_stage("status-clean", work, enabled=True)


def init(repo: Path, install_repo: Path, home: Path | None = None, env: dict | None = None) -> BootstrapReport:
    """Forceful repo-mode injection. Caller passes the target repo; the
    install repo supplies templates and the guard/hook entrypoints."""
    from agent_extensions.install.machine import git_env

    h = Path(home) if home is not None else Path(os.path.expanduser("~"))
    e = env if env is not None else os.environ
    repo = Path(repo)
    report = BootstrapReport()

    probe = git_env_status(repo)
    if probe != 0:
        report.stages.append(StageResult(name="init", status=FAILED, detail=f"not a git repo: {repo}"))
        return report

    # Repo-mode depends on the global excludes covering injected paths.
    report.stages.append(machine_mod.stage_gitignore(h))
    report.stages.append(stage_overlay(repo, install_repo, h))
    backup_root = Path(e.get("AGENTS_BACKUP_DIR") if e and e.get("AGENTS_BACKUP_DIR") else h / ".config" / "agents" / "backups")
    report.stages.append(stage_bindings(repo, install_repo, h, backup_root=backup_root))
    report.stages.append(stage_guards(repo, install_repo, h))
    report.stages.append(stage_status_clean(repo, h))
    return report


def git_env_status(repo: Path) -> int:
    from agent_extensions.install.machine import git_env
    return git_env(Path.home(), "-C", str(repo), "rev-parse", "--is-inside-work-tree").returncode
