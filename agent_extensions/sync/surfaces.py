"""Cloud / manual surfaces: honest capability roster, generated bundles, no false sync."""

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Union

AUTOMATED = "automated"
GENERATED_MANUAL = "generated-manual"
UNSUPPORTED = "unsupported"

SURFACE_MODE = {
    "claude-code": AUTOMATED,
    "codex": AUTOMATED,
    "antigravity": AUTOMATED,
    "local": AUTOMATED,
    "cloud-container": AUTOMATED,
    "claude-web": GENERATED_MANUAL,
    "claude-mobile": GENERATED_MANUAL,
    "web": UNSUPPORTED,
    "mobile-api": UNSUPPORTED,
}


@dataclass
class SurfaceDeclaration:
    """One surface: mode + reason. Automated, manual-bundle, or honest gap."""

    surface: str
    mode: str
    reason: str


@dataclass
class ManualBundle:
    """Upload-ready bundle for a no-filesystem surface: staged copies + checklist."""

    surface: str
    skills: List[str]
    staged_files: List[str] = field(default_factory=list)
    checklist: List[str] = field(default_factory=list)
    digest: str = ""
    secret_clean: bool = False


def declare_surfaces() -> List[SurfaceDeclaration]:
    """Publish exactly which surfaces are automated vs declared gaps."""
    return [
        SurfaceDeclaration(
            surface=name, mode=mode,
            reason={
                AUTOMATED: "bootstrap links + read-back verify",
                GENERATED_MANUAL: "no public API; person uploads the generated bundle via checklist",
                UNSUPPORTED: "no public API exists; declared gap, never claimed complete",
            }[mode],
        )
        for name, mode in SURFACE_MODE.items()
    ]


def read_account_manifest(repo_root: Path) -> Dict:
    """Read the declarative account roster (skills + connectors, never auto-loaded)."""
    return json.loads((repo_root / "bootstrap" / "account-manifest.json").read_text(encoding="utf-8", errors="replace"))


def read_last_applied(repo_root: Path) -> Dict:
    """Read last-applied state; missing file means never applied."""
    path = repo_root / "bootstrap" / "account-manifest.last-applied.json"
    if not path.exists():
        return {"skills": [], "connectors": []}
    return json.loads(path.read_text(encoding="utf-8", errors="replace"))


def diff_manual_state(current: Dict, last_applied: Dict) -> Dict[str, List[str]]:
    """Manual add/remove between roster and last-applied (person executes, tool reports)."""
    cur = {s["name"] for s in current.get("skills", [])}
    last = {s["name"] for s in last_applied.get("skills", [])}
    return {
        "to_add": sorted(cur - last),
        "to_remove": sorted(last - cur),
        "stale": sorted(last - cur),
    }


def is_stale_last_applied(current: Dict, last_applied: Dict) -> bool:
    """Stale when the roster moved since the person last applied it."""
    diff = diff_manual_state(current, last_applied)
    return bool(diff["to_add"] or diff["to_remove"])


def generate_manual_bundle(
    repo_root: Path, surface: str, dest: Path
) -> ManualBundle:
    """Stage real files (never symlinks) + checklist + digest for upload surfaces.

    Secret-scans every staged file before distribution; a hit aborts loudly.
    """
    from agent_extensions.schemas.extension_lock import compute_digest
    from agent_extensions.sync.mcp_governance import scan_secrets

    if SURFACE_MODE.get(surface) != GENERATED_MANUAL:
        raise ValueError(
            f"surface {surface!r} is {SURFACE_MODE.get(surface)}; "
            "manual bundles exist only for generated-manual surfaces"
        )
    manifest = read_account_manifest(repo_root)
    bundle = ManualBundle(surface=surface, skills=[s["name"] for s in manifest["skills"]])
    dest.mkdir(parents=True, exist_ok=True)
    for skill in manifest["skills"]:
        src = repo_root / skill["source"] / "SKILL.md"
        if not src.exists():
            raise FileNotFoundError(f"account skill missing: {skill['source']}")
        text = src.read_text(encoding="utf-8", errors="replace")
        hits = scan_secrets(text)
        if hits:
            raise RuntimeError(f"secret scan failed for {skill['name']}: {hits}")
        staged = dest / skill["name"] / "SKILL.md"
        staged.parent.mkdir(parents=True, exist_ok=True)
        staged.write_text(text, encoding="utf-8")  # real copy, not a symlink
        bundle.staged_files.append(str(staged.relative_to(dest).as_posix()))
        bundle.checklist.append(
            f"upload {skill['name']}/SKILL.md to {surface} account skill settings"
        )
    if not manifest.get("connectors"):
        bundle.checklist.append("connectors: none declared — nothing to connect")
    bundle.digest = compute_digest(json.dumps(sorted(bundle.staged_files), sort_keys=True))
    bundle.secret_clean = True
    (dest / "CHECKLIST.md").write_text(
        "# Manual upload checklist\n\n"
        + "".join(f"- [ ] {item}\n" for item in bundle.checklist)
        + f"\nBundle digest: `{bundle.digest}`\n",
        encoding="utf-8",
    )
    return bundle


def record_last_applied(repo_root: Path, bundle: ManualBundle) -> Path:
    """After the person confirms upload, record the new last-applied state."""
    manifest = read_account_manifest(repo_root)
    path = repo_root / "bootstrap" / "account-manifest.last-applied.json"
    path.write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
    return path
