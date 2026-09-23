"""Machine-level setup stages: gitignore, shims, hooks, cli-shim (issue #53).

Stage semantics are reused verbatim from agent_extensions.sync.bootstrap:
applied | skipped | failed, never silent, failures never stop siblings,
reruns converge. No stage ever raises out to the caller.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from agent_extensions import install
from agent_extensions.sync.bootstrap import APPLIED, FAILED, SKIPPED, StageResult, _run_stage

GIT = "git"


def git_env(home: Path, *args: str) -> subprocess.CompletedProcess:
    """Run a git command scoped to the given HOME (test-isolatable --global config).
    Raises FileNotFoundError when the git binary is absent (thin containers)."""
    env = dict(os.environ, HOME=str(home), USERPROFILE=str(home))
    return subprocess.run([GIT, *args], capture_output=True, text=True, env=env, timeout=60)


def stage_gitignore(home: Path, entries: list[str] | None = None) -> StageResult:
    """Idempotent global excludes: point core.excludesFile at the ignore file,
    then append any missing overlay entries. Additive only — user lines are
    never removed or rewritten. Missing git binary skips (thin containers)."""

    def work() -> str:
        nonlocal target
        target.parent.mkdir(parents=True, exist_ok=True)
        existing = target.read_text(encoding="utf-8") if target.exists() else ""
        have = existing.splitlines()
        missing = [e for e in entries if e not in have]
        if not missing:
            return f"excludes up to date: {target}"
        with target.open("a", encoding="utf-8", newline="\n") as fh:
            if existing and not existing.endswith("\n"):
                fh.write("\n")
            fh.write(install.GITIGNORE_NOTE + "\n")
            fh.writelines(e + "\n" for e in missing)
        return f"appended {len(missing)} entries: {target}"

    probe = _git_available(home)
    if not probe:
        return StageResult(name="gitignore", status=SKIPPED, detail="git unavailable")
    entries = list(entries) if entries is not None else list(install.GITIGNORE_ENTRIES)
    target = _excludes_target(home)
    if target is None:
        return StageResult(name="gitignore", status=FAILED, detail="could not resolve core.excludesFile")
    return _run_stage("gitignore", work, enabled=True)


def _git_available(home: Path) -> bool:
    """True when a git binary answers --version under the given HOME env."""
    try:
        return git_env(home, "--version").returncode == 0
    except (FileNotFoundError, OSError):
        return False


def _excludes_target(home: Path) -> Path | None:
    """Resolve the global excludes file, creating the pointer when unset.
    Note: `git config --global <key>` exits 1 when the key is unset — that is
    not an error. Returns None only when the pointer cannot be established."""
    probe = git_env(home, "config", "--global", "core.excludesFile")
    raw = probe.stdout.strip() if probe.returncode == 0 else ""
    if raw:
        target = Path(os.path.expandvars(raw)).expanduser()
        if not target.is_absolute():
            target = home / target
        return target
    set_result = git_env(home, "config", "--global", "core.excludesFile", str(home / ".config" / "git" / "ignore"))
    if set_result.returncode != 0:
        return None
    return home / ".config" / "git" / "ignore"


# ---- shims (Task 2) ----

from dataclasses import dataclass
from typing import Callable


@dataclass(frozen=True)
class ShimSpec:
    """One harness shim: template file, target path, and presence gate.

    gate(home) returning None means ungated (always applicable); otherwise the
    shim applies only when the gate path exists (capability-gated, never
    creates harness dirs for harnesses this machine does not have).
    """

    name: str
    template: str
    target: Callable[[Path], Path]
    gate: Callable[[Path], Path | None]


def _p(*parts: str) -> Callable[[Path], Path]:
    return lambda home: home.joinpath(*parts)


SHIMS: list[ShimSpec] = [
    ShimSpec("baseline", "AGENTS.md", _p("AGENTS.md"), lambda home: None),
    ShimSpec("pi", "pi-AGENTS.md", _p(".pi", "agent", "AGENTS.md"), _p(".pi", "agent")),
    ShimSpec("claude", "claude-CLAUDE.md", _p(".claude", "CLAUDE.md"), _p(".claude")),
    ShimSpec("codex", "codex-AGENTS.md", _p(".codex", "AGENTS.md"), _p(".codex")),
    ShimSpec("gemini", "gemini-GEMINI.md", _p(".gemini", "GEMINI.md"), _p(".gemini")),
    ShimSpec("kilo", "kilo-HARNESS.md", _p(".config", "kilo", "HARNESS.md"), _p(".config", "kilo")),
]


def render_template(repo_root: Path, name: str) -> str:
    """Read a versioned template from install/templates (marker embedded)."""
    return (Path(repo_root) / "agent_extensions" / "install" / "templates" / name).read_text(encoding="utf-8")


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


def stage_shims(repo_root: Path, home: Path) -> StageResult:
    """Marker-guarded shim install: unmarked existing files are skipped loudly
    (never clobbered); marked files converge to the template; absent targets
    are installed. Baseline AGENTS.md is ungated; harness shims are gated."""

    def work() -> str:
        notes: list[str] = []
        for spec in SHIMS:
            gate = spec.gate(home)
            if gate is not None and not gate.exists():
                notes.append(f"{spec.name}: skipped (gate {gate} absent)")
                continue
            target = spec.target(home)
            rendered = render_template(repo_root, spec.template)
            if target.exists():
                current = target.read_text(encoding="utf-8")
                if install.MANAGED_MD_MARKER not in current:
                    notes.append(f"{spec.name}: skipped (unmanaged file present: {target})")
                    continue
                if current == rendered:
                    notes.append(f"{spec.name}: up to date")
                    continue
                _write_text(target, rendered)
                notes.append(f"{spec.name}: updated")
            else:
                _write_text(target, rendered)
                notes.append(f"{spec.name}: installed")
        return "; ".join(notes)

    return _run_stage("shims", work, enabled=True)


# ---- hooks (Task 3) ----

import json
from datetime import datetime

CLAUDE_HOOK_REL = "agent_extensions/continuity/adapters/claude_hook.py"
PI_EXTENSION_REL = "agent_extensions/continuity/adapters/pi-extension.ts"


def _read_json(path: Path) -> dict:
    """Parse a JSON settings file; corrupt content raises ValueError naming the path."""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"corrupt JSON at {path}: {exc}") from exc


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8", newline="\n")


def backup(path: Path, backup_root: Path) -> Path:
    """Copy a pre-existing file into <backup_root>/install/<timestamp>/ before
    a managed write. Caller only invokes this when a change is imminent, so
    idempotent reruns never create backups (plan ruling R4)."""
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    dest = Path(backup_root) / "install" / stamp / path.name
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(path.read_text(encoding="utf-8"), encoding="utf-8", newline="\n")
    return dest


def _hook_entry(command: str) -> dict:
    return {"hooks": [{"type": "command", "command": command}]}


def stage_hooks(
    install_repo: Path,
    home: Path,
    backup_root: Path | None = None,
) -> StageResult:
    """Wire continuity adapters additively: Claude SessionStart/PreCompact
    hooks and the pi extensions array. JSON merges never touch foreign keys;
    a change is written only when something is actually missing (idempotent);
    corrupt JSON fails the stage naming the path; first write backs up."""

    def work() -> str:
        repo = Path(install_repo)
        notes: list[str] = []
        claude_path = home / ".claude" / "settings.json"
        claude_cmd = "python \"" + (repo / CLAUDE_HOOK_REL).as_posix() + "\""

        current = claude_path.read_text(encoding="utf-8") if claude_path.exists() else None
        if current is not None and _looks_like_json(current):
            data = _read_json(claude_path)
        elif current is None:
            data = {}
        else:
            data = _read_json(claude_path)
        changed_claude = False
        hooks = data.get("hooks")
        if not isinstance(hooks, dict):
            hooks = {}
            data["hooks"] = hooks
            changed_claude = True
        for event in ("SessionStart", "PreCompact"):
            entries = hooks.get(event)
            if not isinstance(entries, list):
                hooks[event] = []
                changed_claude = True
            if not any(
                isinstance(h, dict) and claude_cmd in str(h.get("command", ""))
                for e in hooks[event]
                for h in (e.get("hooks") or [])
            ):
                hooks[event].append(_hook_entry(claude_cmd))
                changed_claude = True
        if changed_claude:
            if current is not None and backup_root is not None:
                backup(claude_path, backup_root)
            _write_json(claude_path, data)
            notes.append("claude: merged")
        else:
            notes.append("claude: already current")

        pi_path = home / ".pi" / "agent" / "settings.json"
        ext_abs = (repo / PI_EXTENSION_REL).resolve().as_posix()
        changed_pi = False
        if pi_path.exists():
            pi_current = pi_path.read_text(encoding="utf-8")
            pi_data = _read_json(pi_path)
        else:
            pi_current = None
            pi_data = {}
            changed_pi = True
        exts = pi_data.get("extensions")
        if not isinstance(exts, list):
            exts = []
            pi_data["extensions"] = exts
            changed_pi = True
        if ext_abs not in exts:
            exts.append(ext_abs)
            changed_pi = True
        if changed_pi:
            if pi_current is not None and backup_root is not None:
                backup(pi_path, backup_root)
            _write_json(pi_path, pi_data)
            notes.append("pi: merged")
        else:
            notes.append("pi: already current")
        return "; ".join(notes)

    return _run_stage("hooks", work, enabled=True)


def _looks_like_json(text: str) -> bool:
    try:
        json.loads(text)
        return True
    except json.JSONDecodeError:
        return False


# ---- cli-shim (Task 4) ----

import shutil

MANAGED_SH_MARKER = "# managed-by: agent-extensions"


def stage_cli_shim(install_repo: Path, home: Path, env: dict | None = None) -> StageResult:
    """Render the everyday `ae` launcher into ~/bin (name via AE_COMMAND).
    Marker-guarded like shims; skipped when ~/bin cannot exist; notes when the
    bin dir is not on PATH so the owner gets the exact export line."""

    def work() -> str:
        e = env if env is not None else os.environ
        name = e.get("AE_COMMAND", "ae")
        bin_dir = home / "bin"
        if not bin_dir.exists():
            try:
                bin_dir.mkdir(parents=True)
            except OSError as exc:
                return f"skipped (cannot create {bin_dir}: {exc})"
        notes: list[str] = []
        rendered = render_template(
            install_repo, "ae-launcher.sh"
        ).replace("{{INSTALL_DIR}}", Path(install_repo).resolve().as_posix())
        target = bin_dir / name
        if target.exists():
            current = target.read_text(encoding="utf-8")
            if MANAGED_SH_MARKER not in current:
                return f"skipped (unmanaged file present: {target})"
            if current == rendered:
                notes.append("up to date")
            else:
                _write_text(target, rendered)
                notes.append("updated")
        else:
            _write_text(target, rendered)
            notes.append("installed")
        if shutil.which(name) is None:
            notes.append(f"NOTE: {bin_dir} not on PATH — add: export PATH=\"{bin_dir}:$PATH\"")
        return f"{name}: " + "; ".join(notes)

    return _run_stage("cli-shim", work, enabled=True)


def bootstrap(install_repo: Path, home: Path | None = None, env: dict | None = None):
    """Machine bootstrap: gitignore → shims → hooks → cli-shim, then the
    capability-bundle sync stages delegated (never duplicated)."""
    from agent_extensions.sync.bootstrap import BootstrapReport

    h = Path(home) if home is not None else Path(os.path.expanduser("~"))
    report = BootstrapReport()
    report.stages.append(stage_gitignore(h))
    report.stages.append(stage_shims(install_repo, h))
    report.stages.append(stage_hooks(install_repo, h))
    report.stages.append(stage_cli_shim(install_repo, h, env))
    return report
