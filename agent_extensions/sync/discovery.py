"""Metadata-first discovery: compact descriptors at routing time, bodies JIT."""

import hashlib
from pathlib import Path
from typing import Dict, List, Optional, Union
from pydantic import BaseModel, Field


class SkillDescriptor(BaseModel):
    """Compact routing-time record — never the body."""

    name: str = Field(..., description="Skill slug, e.g. skill-creator")
    semantic_id: str = Field(..., description="capability.<slug>")
    description: str = Field(..., description="One-line routing blurb from frontmatter")
    body_bytes: int = Field(..., description="Full SKILL.md byte size (budget signal)")
    body_tokens_est: int = Field(..., description="Estimated tokens (~bytes/4)")
    dependencies: List[str] = Field(default_factory=list)
    body_digest: str = Field(..., description="sha256:<hex> of SKILL.md at index time")
    source_path: str = Field(..., description="Repo-relative path to the skill dir")


def _frontmatter_description(skill_md: str) -> str:
    """First `description:` line of the YAML frontmatter, else first heading."""
    in_front = False
    for line in skill_md.splitlines():
        if line.strip() == "---":
            in_front = not in_front
            continue
        if in_front and line.startswith("description:"):
            return line.split(":", 1)[1].strip()
    for line in skill_md.splitlines():
        if line.startswith("# "):
            return line[2:].strip()
    return ""


def _skill_dependencies(skill_dir: Path) -> List[str]:
    """Subdirectory names that look like loadable body assets (agents/references)."""
    deps = []
    for child in sorted(skill_dir.iterdir()):
        if child.is_dir() and child.name in ("agents", "references", "assets", "scripts"):
            deps.append(child.name)
    return deps


def index_skill(skill_dir: Path, repo_root: Path) -> SkillDescriptor:
    """Build a descriptor from a live skill dir without loading the body."""
    skill_md = (skill_dir / "SKILL.md").read_text(encoding="utf-8", errors="replace")
    raw = skill_md.encode()
    slug = skill_dir.name
    return SkillDescriptor(
        name=slug,
        semantic_id=f"capability.{slug}",
        description=_frontmatter_description(skill_md),
        body_bytes=len(raw),
        body_tokens_est=max(1, len(raw) // 4),
        dependencies=_skill_dependencies(skill_dir),
        body_digest="sha256:" + hashlib.sha256(raw).hexdigest(),
        source_path=skill_dir.relative_to(repo_root).as_posix(),
    )


def index_repo(repo_root: Union[str, Path]) -> List[SkillDescriptor]:
    """Index every skill in plugins/*/skills — descriptors only, bodies untouched."""
    root = Path(repo_root)
    descriptors = []
    for skills_dir in sorted((root / "plugins").glob("*/skills")):
        for skill_dir in sorted(skills_dir.iterdir()):
            if skill_dir.is_dir() and (skill_dir / "SKILL.md").exists():
                descriptors.append(index_skill(skill_dir, root))
    return descriptors


def resolve_body(
    descriptor: SkillDescriptor,
    repo_root: Union[str, Path],
    *,
    expected_digest: Optional[str] = None,
) -> str:
    """Load the full body JIT; fail when the digest moved since index time.

    Args:
        descriptor: The routing-time descriptor being resolved.
        repo_root: Repo root the descriptor's source_path is relative to.
        expected_digest: Override digest check (defaults to descriptor's own).

    Raises:
        FileNotFoundError: Body missing on disk (selected skill, no body).
        ValueError: Body hash changed since indexing.
    """
    body_path = Path(repo_root) / descriptor.source_path / "SKILL.md"
    if not body_path.exists():
        raise FileNotFoundError(
            f"selected skill {descriptor.name} has no body at {body_path}"
        )
    body = body_path.read_text(encoding="utf-8", errors="replace")
    digest = "sha256:" + hashlib.sha256(body.encode()).hexdigest()
    want = expected_digest or descriptor.body_digest
    if digest != want:
        raise ValueError(
            f"body hash changed for {descriptor.name}: indexed {want[:20]}… "
            f"but disk is {digest[:20]}…; re-index before loading"
        )
    return body


def discovery_index(descriptors: List[SkillDescriptor]) -> Dict[str, SkillDescriptor]:
    """Routing table keyed by semantic ID; duplicate capabilities fail loudly."""
    table: Dict[str, SkillDescriptor] = {}
    for desc in descriptors:
        if desc.semantic_id in table:
            raise ValueError(f"duplicate capability in discovery index: {desc.semantic_id}")
        table[desc.semantic_id] = desc
    return table
