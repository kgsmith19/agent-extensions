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
