"""Build extensions.lock from live sources; preserve existing pins verbatim."""

import json
from pathlib import Path
from typing import Union
from agent_extensions.schemas.extension_lock import (
    ExtensionsLock,
    ExtensionLock,
    compute_digest,
)

# Default provenance for vendored skills whose VENDORED-FROM line predates the
# optional <source-url> <spdx> columns.
DEFAULT_SKILL_SOURCE_URL = "https://github.com/anthropics/skills"


def _rendered_digest_for_skill(skill_dir: Path) -> str:
    """Digest of the canonical skill inventory (SKILL.md + LICENSE.txt)."""
    skill_md = skill_dir / "SKILL.md"
    license_txt = skill_dir / "LICENSE.txt"
    canonical = json.dumps(
        {
            "skill_md": skill_md.read_text() if skill_md.exists() else "",
            "license_txt": license_txt.read_text() if license_txt.exists() else "",
        },
        sort_keys=True,
    )
    return compute_digest(canonical)


def _license_from_file(skill_dir: Path) -> str:
    """SPDX id inferred from a skill's LICENSE.txt; Apache-2.0 when absent/unknown."""
    lic = skill_dir / "LICENSE.txt"
    if lic.exists():
        head = lic.read_text()[:2000]
        if "MIT License" in head:
            return "MIT"
        if "Apache License" in head:
            return "Apache-2.0"
    return "Apache-2.0"


def build_lock_from_repo(repo_root: Union[str, Path]) -> ExtensionsLock:
    """Read every locked source live and build the lockfile.

    - Own skills: source = local skill dir, commit = VENDORED-FROM pin,
      license = LICENSE.txt header (Apache-2.0), digest = rendered content.
    - External marketplaces: pins + resolvedCommits copied verbatim from
      bootstrap/external-marketplaces.json (never renegotiated here).
    """
    from agent_extensions.schemas.license_metadata import COMMON_SPDX_LICENSES

    root = Path(repo_root)
    entries = []

    # 1. Own vendored skills (live read of VENDORED-FROM + LICENSE.txt).
    for skills_dir in sorted((root / "plugins").glob("*/skills")):
        vendored = {}
        vf = skills_dir / "VENDORED-FROM"
        if vf.exists():
            for line in vf.read_text().splitlines():
                parts = line.split()
                if len(parts) >= 2:
                    vendored[parts[0]] = {
                        "commit": parts[1],
                        "source_url": (
                            parts[3] if len(parts) >= 4 else DEFAULT_SKILL_SOURCE_URL
                        ),
                        "license": parts[4] if len(parts) >= 5 else None,
                    }
        for skill_dir in sorted(skills_dir.iterdir()):
            if not skill_dir.is_dir() or not (skill_dir / "SKILL.md").exists():
                continue
            meta = vendored.get(skill_dir.name, {})
            license_id = meta.get("license") or _license_from_file(skill_dir)
            assert license_id in COMMON_SPDX_LICENSES
            entries.append(
                ExtensionLock(
                    identity=f"capability.{skill_dir.name}",
                    source_url=meta.get("source_url", DEFAULT_SKILL_SOURCE_URL),
                    source_commit=meta.get("commit", "0" * 40),
                    spdx_license=license_id,
                    rendered_digest=_rendered_digest_for_skill(skill_dir),
                    upstream_kind="pinned",
                )
            )

    # 2. External marketplace pins, preserved verbatim.
    mp_path = root / "bootstrap" / "external-marketplaces.json"
    mp_data = json.loads(mp_path.read_text())
    for mp in mp_data.get("marketplaces", []):
        entries.append(
            ExtensionLock(
                identity=f"marketplace.{mp['name']}",
                source_url=f"https://github.com/{mp['repo']}",
                source_commit=mp["pinnedCommit"],
                spdx_license="Apache-2.0",
                rendered_digest=compute_digest(
                    json.dumps(mp.get("plugins", []), sort_keys=True, default=str)
                ),
                upstream_kind="pinned",
            )
        )
        for plugin in mp.get("plugins", []):
            if isinstance(plugin, dict) and plugin.get("resolvedCommit"):
                entries.append(
                    ExtensionLock(
                        identity=f"plugin.{mp['name']}.{plugin['name']}",
                        source_url=f"https://github.com/{mp['repo']}",
                        source_commit=plugin["resolvedCommit"],
                        spdx_license="Apache-2.0",
                        rendered_digest=compute_digest(
                            json.dumps(plugin, sort_keys=True, default=str)
                        ),
                        upstream_kind="pinned",
                    )
                )

    # 3. Claude live-marketplace pointer (documented distinction: this repo
    # tracks it by name only; Codex/Antigravity consume the pinned commits
    # above. The pointer itself is not a reproducible lock.)
    entries.append(
        ExtensionLock(
            identity="marketplace.agent-extensions-live",
            source_url="claude-code-marketplace:agent-extensions",
            source_commit="0" * 40,
            spdx_license="Apache-2.0",
            rendered_digest=compute_digest("live-marketplace-pointer"),
            upstream_kind="live-marketplace",
        )
    )

    return ExtensionsLock(entries=entries)
