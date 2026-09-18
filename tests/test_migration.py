import json
from pathlib import Path

import pytest
from agent_extensions.sync.migrate import (
    generate_catalog_view,
    generate_profile_view,
    migrate_marketplace,
)


def test_migrate_marketplace_preserves_pins_and_versions():
    """Protects existing state; migration keeps versions, pins, and licenses verbatim."""
    declarations = [
        {
            "name": "canvas-design",
            "source": "plugins/anthropic-product-skills/skills/canvas-design",
            "version": "1.0.0",
            "source_pin": "0a64e398ec6bb34a494f0c347e8ccae53a862f8e",
            "license": "Apache-2.0",
            "provider": "anthropic",
        },
        {
            "name": "skill-creator",
            "source": "plugins/general-skills/skills/skill-creator",
            "version": "1.0.0",
            "source_pin": "0a64e398ec6bb34a494f0c347e8ccae53a862f8e",
            "license": "Apache-2.0",
            "provider": "anthropic",
        },
    ]
    catalog = migrate_marketplace(declarations)
    by_name = {e.name: e for e in catalog.entries}
    assert by_name["canvas-design"].source_pin == "0a64e398ec6bb34a494f0c347e8ccae53a862f8e"
    assert by_name["canvas-design"].version == "1.0.0"
    assert by_name["canvas-design"].license == "Apache-2.0"
    assert by_name["skill-creator"].source_pin == "0a64e398ec6bb34a494f0c347e8ccae53a862f8e"


def test_migrate_marketplace_rejects_duplicate_capability():
    """Protects migration integrity; duplicate declarations fail loudly."""
    declarations = [
        {
            "name": "a",
            "source": "plugins/x/a",
            "version": "1.0.0",
            "source_pin": "sha256:abc123...",
            "license": "MIT",
            "provider": "anthropic",
        },
        {
            "name": "b",
            "source": "plugins/x/b",
            "version": "1.0.0",
            "source_pin": "sha256:def456...",
            "license": "MIT",
            "provider": "anthropic",
            "semantic_id": "capability.a",
        },
    ]
    # Force both to the same semantic ID via explicit override on first too.
    declarations[0]["semantic_id"] = "capability.a"
    with pytest.raises(ValueError, match="duplicate|conflicting"):
        migrate_marketplace(declarations)


def test_migrate_marketplace_derives_semantic_id_from_skill_name():
    """Protects naming discipline; skill names map to capability.<slug> IDs."""
    catalog = migrate_marketplace(
        [
            {
                "name": "web-artifacts-builder",
                "source": "plugins/anthropic-product-skills/skills/web-artifacts-builder",
                "version": "1.0.0",
                "source_pin": "0a64e398ec6bb34a494f0c347e8ccae53a862f8e",
                "license": "Apache-2.0",
                "provider": "anthropic",
            }
        ]
    )
    assert catalog.entries[0].semantic_id == "capability.web-artifacts-builder"


def test_generated_views_round_trip(tmp_path: Path):
    """Protects view generation; written catalog/profile JSON reads back cleanly."""
    from agent_extensions.sync.read_back import (
        read_catalog_from_filesystem,
        read_profile_from_filesystem,
    )
    from agent_extensions.sync.selection import select_for_task

    catalog = migrate_marketplace(
        [
            {
                "name": "canvas-design",
                "source": "plugins/anthropic-product-skills/skills/canvas-design",
                "version": "1.0.0",
                "source_pin": "0a64e398ec6bb34a494f0c347e8ccae53a862f8e",
                "license": "Apache-2.0",
                "provider": "anthropic",
            },
            {
                "name": "skill-creator",
                "source": "plugins/general-skills/skills/skill-creator",
                "version": "1.0.0",
                "source_pin": "0a64e398ec6bb34a494f0c347e8ccae53a862f8e",
                "license": "Apache-2.0",
                "provider": "anthropic",
            },
        ]
    )
    profile = select_for_task(catalog, ["capability.canvas-design"])

    catalog_path = tmp_path / "catalog.json"
    profile_path = tmp_path / "profile.json"
    generate_catalog_view(catalog, catalog_path)
    generate_profile_view(profile, profile_path)

    assert json.loads(catalog_path.read_text())["entries"][0]["semantic_id"].startswith(
        "capability."
    )
    assert len(read_catalog_from_filesystem(catalog_path).entries) == 2
    assert len(read_profile_from_filesystem(profile_path).entries) == 1


def test_three_skill_account_manifest_compatibility():
    """Protects account parity; the 3-skill roster maps 1:1 onto the migrated catalog."""
    manifest = json.loads(Path("bootstrap/account-manifest.json").read_text())
    skill_names = sorted(s["name"] for s in manifest["skills"])
    assert skill_names == ["canvas-design", "skill-creator", "web-artifacts-builder"]

    catalog = migrate_marketplace(
        [
            {
                "name": name,
                "source": next(
                    s["source"] for s in manifest["skills"] if s["name"] == name
                ),
                "version": "1.0.0",
                "source_pin": "preserve-me",
                "license": "Apache-2.0",
                "provider": "anthropic",
            }
            for name in skill_names
        ]
    )
    catalog_ids = sorted(e.semantic_id for e in catalog.entries)
    assert catalog_ids == [f"capability.{n}" for n in skill_names]
