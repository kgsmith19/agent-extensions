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


def _converge_our_entries(entries: list, command: str, needle: str) -> tuple[list, bool]:
    """Return (entries, changed) with exactly one entry running our hook
    (needle found in a command = ours by content), pointed at the current
    install path. Foreign entries are preserved untouched; stale/duplicate
    ours entries collapse into one."""
    result: list = []
    ours_done = False
    changed = False
    for e in entries:
        cmds = [str(h.get("command", "")) for h in (e.get("hooks") or []) if isinstance(h, dict)]
        if any(needle in c for c in cmds):
            if ours_done:
                changed = True
                continue  # duplicate ours from another run/checkout -> drop
            ours_done = True
            if command not in cmds:
                result.append({"hooks": [{"type": "command", "command": command}]})
                changed = True
            else:
                result.append(e)
            continue
        result.append(e)
    if not ours_done:
        result.append({"hooks": [{"type": "command", "command": command}]})
        changed = True
    return result, changed


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
            new_entries, conv_changed = _converge_our_entries(
                hooks[event], claude_cmd, "claude_hook.py"
            )
            if conv_changed:
                hooks[event] = new_entries
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
        ours = [x for x in exts if isinstance(x, str) and "pi-extension.ts" in x]
        if ours != [ext_abs]:
            pi_data["extensions"] = [x for x in exts if not (isinstance(x, str) and "pi-extension.ts" in x)] + [ext_abs]
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


WIN_PATH_ENTRY = "%USERPROFILE%\\bin"


class PathEntryResult:
    """Outcome of a user-PATH ensure: appended or already present."""

    def __init__(self, appended: bool, before: str, after: str = ""):
        self.appended = appended
        self.before = before
        self.after = after


def _user_path_reader() -> str:
    """Read the USER-scope PATH from the registry (raw, keeps %VAR% tokens)."""
    try:
        proc = subprocess.run(
            ["reg", "query", "HKCU\\Environment", "/v", "Path"],
            capture_output=True, text=True, timeout=30,
        )
    except (FileNotFoundError, OSError):
        return ""
    if proc.returncode != 0:
        return ""
    for line in proc.stdout.splitlines():
        if "Path" in line and "REG_" in line:
            cols = line.split(None, 3)
            return cols[3] if len(cols) > 3 else ""
    return ""


def _user_path_writer(new_value: str) -> None:
    """Append-only write of the USER PATH, preserving REG_EXPAND_SZ so %VAR%
    tokens keep expanding (a plain SetEnvironmentVariable rewrite would
    flatten them)."""
    proc = subprocess.run(
        ["reg", "add", "HKCU\\Environment", "/v", "Path", "/t", "REG_EXPAND_SZ", "/d", new_value, "/f"],
        capture_output=True, text=True, timeout=30,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"reg add failed: {proc.stderr.strip()[:200]}")


def ensure_user_path_entry(entry: str, reader=None, writer=None) -> PathEntryResult:
    """Idempotent, additive: append `entry` to the USER PATH exactly once.
    Callables are injectable for tests; defaults use the raw registry so
    %VAR% references in the existing value are never expanded or lost."""
    read = reader or _user_path_reader
    write = writer or _user_path_writer
    current = read() or ""
    parts = [p.strip().strip("\"") for p in current.split(";") if p.strip()]
    norm = entry.lower().rstrip("\\")
    if any(p.lower().rstrip("\\") == norm for p in parts):
        return PathEntryResult(False, current, current)
    new_value = (current.rstrip(";") + ";" + entry) if current.strip() else entry
    write(new_value)
    return PathEntryResult(True, current, new_value)


def stage_cli_shim(install_repo: Path, home: Path, env: dict | None = None) -> StageResult:
    """Render the everyday `ae` launcher set into ~/bin — bash, cmd, and
    PowerShell variants (Windows resolves `ae` via ae.cmd; POSIX via ae) —
    then make the bin dir discoverable: user-PATH registry entry on Windows
    (idempotent, %VAR%-preserving), ~/.profile + ~/.bashrc export on POSIX.
    Marker-guarded like shims; skipped when ~/bin cannot exist."""

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
        install_abs = Path(install_repo).resolve().as_posix()
        specs = [("ae", "ae-launcher.sh"), (name + ".cmd", "ae-launcher.cmd"), (name + ".ps1", "ae-launcher.ps1")]
        for target_name, template_name in specs:
            rendered = render_template(install_repo, template_name).replace("{{INSTALL_DIR}}", install_abs)
            target = bin_dir / target_name
            if target.exists():
                current = target.read_text(encoding="utf-8")
                if MANAGED_SH_MARKER not in current:
                    notes.append(f"{target_name}: skipped (unmanaged file present: {target})")
                    continue
                if current == rendered:
                    notes.append(f"{target_name}: up to date")
                else:
                    _write_text(target, rendered)
                    notes.append(f"{target_name}: updated")
            else:
                _write_text(target, rendered)
                notes.append(f"{target_name}: installed")
        posix_path_note = f"NOTE: {bin_dir} not on PATH — add: export PATH=\"{bin_dir}:$PATH\""
        if os.name == "nt":
            try:
                result = ensure_user_path_entry(WIN_PATH_ENTRY)
                notes.append(
                    "user PATH: appended %USERPROFILE%\\bin (new terminals pick it up)"
                    if result.appended
                    else "user PATH: already contains %USERPROFILE%\\bin"
                )
            except Exception as exc:  # noqa: BLE001 — report, never raise out of the stage
                notes.append(f"user PATH: failed ({exc}); {posix_path_note}")
        else:
            notes.append(posix_path_note)
        return "; ".join(notes)

    return _run_stage("cli-shim", work, enabled=True)


def bootstrap(install_repo: Path, home: Path | None = None, env: dict | None = None):
    """Machine bootstrap: gitignore → shims → hooks → cli-shim, then the
    capability-bundle sync stages delegated (never duplicated)."""
    from agent_extensions.sync.bootstrap import BootstrapReport, bootstrap as sync_bootstrap

    h = Path(home) if home is not None else Path(os.path.expanduser("~"))
    report = BootstrapReport()
    report.stages.append(stage_gitignore(h))
    report.stages.append(stage_shims(install_repo, h))
    report.stages.append(stage_hooks(install_repo, h))
    report.stages.append(stage_cli_shim(install_repo, h, env))
    report.stages.extend(sync_bootstrap(install_repo, home=h).stages)
    return report
