"""Migration: convert current marketplace declarations to a versioned Catalog."""

import json
import re
from pathlib import Path
from typing import List, Union
from agent_extensions.schemas.catalog_schema import Catalog, CatalogEntry
from agent_extensions.schemas.profile_schema import Profile


def _slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.strip().lower()).strip("-")
    return slug or "unnamed"


def migrate_marketplace(declarations: List[dict]) -> Catalog:
    """Convert marketplace skill declarations into a validated Catalog.

    Each declaration needs: name, source, version, source_pin, license.
    Optional: provider (default "anthropic"), description, semantic_id
    (default "capability.<slug-of-name>"), disabled.

    Pins, versions, and licenses are preserved verbatim — migration never
    re-pins or renegotiates. Duplicates/conflicts raise via Catalog validation.

    Raises:
        ValueError: On missing required fields or duplicate/conflicting IDs.
    """
    entries = []
    for decl in declarations:
        for field in ("name", "source", "version", "source_pin", "license"):
            if not decl.get(field):
                raise ValueError(
                    f"marketplace declaration {decl.get('name', '?')!r} "
                    f"missing required field: {field}"
                )
        semantic_id = decl.get("semantic_id") or f"capability.{_slugify(decl['name'])}"
        entries.append(
            CatalogEntry(
                semantic_id=semantic_id,
                name=decl["name"],
                provider=decl.get("provider", "anthropic"),
                version=decl["version"],
                source_pin=decl["source_pin"],
                license=decl["license"],
                description=decl.get("description") or f"Migrated from {decl['source']}",
                disabled=bool(decl.get("disabled", False)),
            )
        )
    return Catalog(entries=entries)


def generate_catalog_view(catalog: Catalog, dest: Union[str, Path]) -> Path:
    """Write the broad-supply catalog view as JSON; return the path."""
    path = Path(dest)
    path.write_text(json.dumps(catalog.model_dump(mode="json"), indent=2, sort_keys=True))
    return path


def generate_profile_view(profile: Profile, dest: Union[str, Path]) -> Path:
    """Write the tiny task-specific profile view as JSON; return the path."""
    path = Path(dest)
    path.write_text(json.dumps(profile.model_dump(mode="json"), indent=2, sort_keys=True))
    return path
