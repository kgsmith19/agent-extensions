"""Universal bootstrap: one idempotent entrypoint, per-stage applied/skipped/failed."""

import os
import shutil
import stat
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional

APPLIED = "applied"
SKIPPED = "skipped"
FAILED = "failed"

SECRET_MARKERS = ("sk-", "sk-ant-", "xoxb-", "ghp_", "AKIA", "AIza")


@dataclass
class StageResult:
    """One shared semantic stage's outcome — never silent, never blocking siblings."""

    name: str
    status: str  # applied | skipped | failed
    detail: str = ""


@dataclass
class BootstrapReport:
    """Full bootstrap run: one entry per stage, secrets never included."""

    stages: List[StageResult] = field(default_factory=list)

    def by_name(self, name: str) -> Optional[StageResult]:
        for stage in self.stages:
            if stage.name == name:
                return stage
        return None

    def failed(self) -> List[StageResult]:
        return [s for s in self.stages if s.status == FAILED]

    def ok(self) -> bool:
        return not self.failed()


def _check_no_secrets(text: str) -> Optional[str]:
    for marker in SECRET_MARKERS:
        if marker in text:
            return f"refusing to write generated config containing secret marker {marker!r}"
    return None


def detect_surfaces(repo_root: Path, home: Optional[Path] = None) -> Dict[str, Path]:
    """Detect available provider surfaces without creating anything.

    Returns map of surface name -> target dir for surfaces whose parent
    chain is writable. Absent/unwritable surfaces are simply omitted
    (callers report them as skipped, never failed).
    """
    home = home or Path(os.path.expanduser("~"))
    candidates = {
        "claude-code": home / ".claude" / "plugins",
        "codex": home / ".agents" / "skills",
        "antigravity": home / ".gemini" / "config" / "plugins",
        "local": repo_root / ".bootstrap-out",
    }
    available = {}
    for name, target in candidates.items():
        parent = target.parent
        try:
            parent.mkdir(parents=True, exist_ok=True)
            if os.access(parent, os.W_OK):
                available[name] = target
        except OSError:
            continue
    return available


def _run_stage(
    name: str,
    fn: Callable[[], str],
    *,
    enabled: bool,
    skip_reason: str = "",
) -> StageResult:
    if not enabled:
        return StageResult(name=name, status=SKIPPED, detail=skip_reason)
    try:
        return StageResult(name=name, status=APPLIED, detail=fn())
    except Exception as exc:  # noqa: BLE001 — each stage must report, never raise
        return StageResult(name=name, status=FAILED, detail=f"{type(exc).__name__}: {exc}")


def _link_skill(source: Path, link: Path) -> None:
    """Idempotent live link: existing correct link is a no-op; stale link repaired."""
    if link.is_symlink() or (os.name == "nt" and link.is_junction()):
        try:
            if os.readlink(link) == os.fspath(source):
                return
        except OSError:
            pass
        link.unlink()
    elif link.exists():
        raise RuntimeError(f"refusing to overwrite non-link at {link}")
    link.parent.mkdir(parents=True, exist_ok=True)
    try:
        link.symlink_to(source, target_is_directory=True)
    except OSError:
        if os.name == "nt":
            import _winapi  # type: ignore

            _winapi.CreateJunction(os.fspath(source), os.fspath(link))
        else:
            raise


def bootstrap(
    repo_root: Path,
    *,
    selected_ids: Optional[List[str]] = None,
    home: Optional[Path] = None,
    surfaces: Optional[Dict[str, Path]] = None,
) -> BootstrapReport:
    """Idempotent capability-gated bootstrap.

    Stages (shared semantics on every OS): detect → locks → catalog →
    profile → render → link → read-back. Each stage reports
    applied/skipped/failed independently; failures never stop siblings.
    Reruns converge: linking is a no-op when already correct.
    """
    from agent_extensions.sync.locks import build_lock_from_repo
    from agent_extensions.schemas.extension_lock import verify_lockfile
    from agent_extensions.sync.migrate import migrate_marketplace
    from agent_extensions.sync.selection import select_for_task
    from agent_extensions.sync.render import render_profile_to_manifest
    from agent_extensions.schemas.extension_lock import compute_digest
    import json

    repo_root = Path(repo_root)
    report = BootstrapReport()
    state: Dict[str, object] = {}

    available = surfaces if surfaces is not None else detect_surfaces(repo_root, home)

    def do_detect() -> str:
        state["surfaces"] = available
        return f"surfaces: {sorted(available)}"

    report.stages.append(
        _run_stage("detect", do_detect, enabled=True)
    )

    def do_locks() -> str:
        lock = build_lock_from_repo(repo_root)
        problems = verify_lockfile(lock)
        if problems:
            raise RuntimeError("; ".join(problems))
        state["lock"] = lock
        return f"{len(lock.entries)} locks verified"

    report.stages.append(_run_stage("locks", do_locks, enabled=True))

    def do_catalog() -> str:
        skills = []
        for skills_dir in sorted((repo_root / "plugins").glob("*/skills")):
            vf = skills_dir / "VENDORED-FROM"
            pins = {}
            if vf.exists():
                for line in vf.read_text(encoding="utf-8", errors="replace").splitlines():
                    parts = line.split()
                    if len(parts) >= 2:
                        pins[parts[0]] = parts[1]
            for skill_dir in sorted(skills_dir.iterdir()):
                if skill_dir.is_dir() and (skill_dir / "SKILL.md").exists():
                    skills.append(
                        {
                            "name": skill_dir.name,
                            "source": str(
                                skill_dir.relative_to(repo_root).as_posix()
                            ),
                            "version": "1.0.0",
                            "source_pin": pins.get(skill_dir.name, "0" * 40),
                            "license": "Apache-2.0",
                            "provider": "anthropic",
                        }
                    )
        catalog = migrate_marketplace(skills)
        state["catalog"] = catalog
        return f"{len(catalog.entries)} catalog entries"

    report.stages.append(_run_stage("catalog", do_catalog, enabled=True))

    def do_profile() -> str:
        catalog = state.get("catalog")
        if catalog is None:
            raise RuntimeError("catalog stage did not produce a catalog")
        ids = selected_ids
        if ids is None:
            # Default demo footprint: every skill in the catalog (still tiny:
            # this repo's full supply is 3 skills). Explicit callers pass
            # selected_ids for true task-sized subsets.
            ids = [e.semantic_id for e in catalog.entries]
        profile = select_for_task(catalog, ids)
        state["profile"] = profile
        return f"{len(profile.entries)} profile entries"

    report.stages.append(_run_stage("profile", do_profile, enabled=True))

    def do_render() -> str:
        manifest = render_profile_to_manifest(state["catalog"], state["profile"])
        digest = compute_digest(json.dumps(manifest, sort_keys=True, default=str))
        secret_problem = _check_no_secrets(json.dumps(manifest, default=str))
        if secret_problem:
            raise RuntimeError(secret_problem)
        state["manifest"] = manifest
        state["digest"] = digest
        return digest

    report.stages.append(_run_stage("render", do_render, enabled=True))

    def do_link() -> str:
        manifest = state.get("manifest")
        if manifest is None:
            raise RuntimeError("render stage did not produce a manifest")
        linked, skipped = 0, 0
        for surface, target in available.items():
            for bucket in ("active", "candidate"):
                for item in manifest.get(bucket, []):
                    slug = item["semantic_id"].removeprefix("capability.")
                    source = repo_root / "plugins" / "*" / "skills" / slug
                    matches = sorted(repo_root.glob(f"plugins/*/skills/{slug}"))
                    if not matches:
                        skipped += 1
                        continue
                    _link_skill(matches[0], target / slug)
                    linked += 1
        return f"{linked} linked, {skipped} skipped (surface={sorted(available)})"

    report.stages.append(
        _run_stage(
            "link",
            do_link,
            enabled=bool(available),
            skip_reason="no writable surfaces detected",
        )
    )

    def do_read_back() -> str:
        manifest = state.get("manifest")
        if manifest is None:
            raise RuntimeError("render stage did not produce a manifest")
        active = len(manifest.get("active", []))
        candidate = len(manifest.get("candidate", []))
        if active + candidate != len(state["profile"].entries):
            raise RuntimeError(
                f"drift: manifest holds {active + candidate} entries "
                f"but profile selected {len(state['profile'].entries)}"
            )
        return f"read-back matches: {active} active + {candidate} candidate"

    report.stages.append(_run_stage("read-back", do_read_back, enabled=True))
    return report


def rollback_links(targets: List[Path]) -> StageResult:
    """Remove links created by bootstrap; missing targets are skipped, not failed."""
    removed, missing = 0, 0
    for target in targets:
        try:
            if target.is_symlink() or (os.name == "nt" and target.is_junction()):
                target.unlink()
                removed += 1
            elif not target.exists():
                missing += 1
            else:
                return StageResult(
                    name="rollback",
                    status=FAILED,
                    detail=f"refusing to remove non-link at {target}",
                )
        except OSError as exc:
            return StageResult(name="rollback", status=FAILED, detail=str(exc))
    return StageResult(
        name="rollback", status=APPLIED, detail=f"{removed} removed, {missing} already absent"
    )


def is_read_only(path: Path) -> bool:
    """True when path exists and no write bit is set (best-effort on Windows)."""
    try:
        mode = path.stat().st_mode
    except OSError:
        return False
    return not (mode & (stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH))
