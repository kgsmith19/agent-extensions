import json
import tempfile
from pathlib import Path

import pytest

from agent_extensions.sync.read_back import (
    offline_bootstrap,
    read_catalog_from_filesystem,
    read_profile_from_filesystem,
)
from agent_extensions.sync.render import (
    apply_provider_binding,
    render_profile_to_manifest,
    validate_profile_against_catalog,
)
from agent_extensions.schemas.catalog_schema import Catalog, CatalogEntry
from agent_extensions.schemas.profile_schema import Profile, ProfileEntry, LifecycleState


def _catalog() -> Catalog:
    return Catalog(
        entries=[
            CatalogEntry(
                semantic_id="capability.search",
                name="Web Search",
                provider="anthropic",
                version="1.0.0",
                source_pin="sha256:abc123...",
                license="MIT",
            ),
        ]
    )


def test_read_back_reconstructs_catalog_from_filesystem():
    """Protects read-back contract; catches data loss during filesystem I/O."""
    with tempfile.TemporaryDirectory() as tmpdir:
        catalog_file = Path(tmpdir) / "catalog.json"
        catalog_data = {
            "version": "1.0.0",
            "entries": [
                {
                    "semantic_id": "capability.search",
                    "name": "Web Search",
                    "provider": "anthropic",
                    "version": "1.0.0",
                    "source_pin": "sha256:abc123...",
                    "license": "MIT",
                }
            ],
        }

        catalog_file.write_text(json.dumps(catalog_data))

        catalog = read_catalog_from_filesystem(str(catalog_file))

        assert len(catalog.entries) == 1
        assert catalog.entries[0].semantic_id == "capability.search"
        assert catalog.entries[0].source_pin == "sha256:abc123..."


def test_read_back_round_trips_profile_state():
    """Protects profile I/O; lifecycle state survives filesystem round-trip."""
    with tempfile.TemporaryDirectory() as tmpdir:
        profile_file = Path(tmpdir) / "profile.json"
        profile_file.write_text(
            json.dumps(
                {
                    "version": "1.0.0",
                    "entries": [
                        {
                            "semantic_id": "capability.search",
                            "state": "RESIDENT",
                            "provider_binding": "anthropic-1.0.0",
                        }
                    ],
                }
            )
        )

        profile = read_profile_from_filesystem(str(profile_file))

        assert len(profile.entries) == 1
        assert profile.entries[0].state is LifecycleState.RESIDENT


def test_render_categorizes_active_and_candidate():
    """Protects render contract; RESIDENT/INVOKED land in active, rest in candidate."""
    catalog = _catalog()
    profile = Profile(
        entries=[
            ProfileEntry(semantic_id="capability.search", state=LifecycleState.RESIDENT),
        ]
    )

    manifest = render_profile_to_manifest(catalog, profile)

    assert len(manifest["active"]) == 1
    assert len(manifest["candidate"]) == 0
    assert manifest["active"][0]["source_pin"] == "sha256:abc123..."


def test_render_rejects_missing_catalog_entry():
    """Protects data integrity; rejects profiles referencing missing catalog entries."""
    catalog = Catalog(entries=[])

    profile = Profile(
        entries=[
            ProfileEntry(
                semantic_id="capability.missing",
                state=LifecycleState.ENABLED,
            )
        ]
    )

    with pytest.raises(ValueError, match="missing catalog entry"):
        render_profile_to_manifest(catalog, profile)


def test_validate_profile_against_catalog():
    """Protects consistency; validates profile entries exist in catalog."""
    catalog = _catalog()

    profile = Profile(
        entries=[
            ProfileEntry(semantic_id="capability.search", state=LifecycleState.ENABLED),
            ProfileEntry(semantic_id="capability.missing", state=LifecycleState.AVAILABLE),
        ]
    )

    is_valid, missing = validate_profile_against_catalog(catalog, profile)
    assert not is_valid
    assert "capability.missing" in missing


def test_apply_provider_binding_with_override():
    """Protects provider mapping; explicit binding map wins over default."""
    catalog = _catalog()
    profile = Profile(
        entries=[
            ProfileEntry(semantic_id="capability.search", state=LifecycleState.RESIDENT),
        ]
    )
    manifest = render_profile_to_manifest(catalog, profile)

    bound = apply_provider_binding(
        manifest,
        "openai",
        binding_map={"capability.search": "openai-search-v2"},
    )

    assert bound["provider"] == "openai"
    assert bound["active"][0]["provider_binding"] == "openai-search-v2"


def test_apply_provider_binding_default_format():
    """Protects provider mapping; default binding is provider-version."""
    catalog = _catalog()
    profile = Profile(
        entries=[
            ProfileEntry(semantic_id="capability.search", state=LifecycleState.RESIDENT),
        ]
    )
    manifest = render_profile_to_manifest(catalog, profile)

    bound = apply_provider_binding(manifest, "anthropic")

    assert bound["active"][0]["provider_binding"] == "anthropic-1.0.0"


def test_offline_bootstrap():
    """Protects hermetic operation; bootstrap works without network."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir = Path(tmpdir)

        (tmpdir / "catalog.json").write_text(
            json.dumps(
                {
                    "version": "1.0.0",
                    "entries": [
                        {
                            "semantic_id": "capability.search",
                            "name": "Web Search",
                            "provider": "anthropic",
                            "version": "1.0.0",
                            "source_pin": "sha256:abc123...",
                            "license": "MIT",
                        }
                    ],
                }
            )
        )

        (tmpdir / "profile.json").write_text(
            json.dumps(
                {
                    "version": "1.0.0",
                    "entries": [
                        {
                            "semantic_id": "capability.search",
                            "state": "ENABLED",
                            "provider_binding": None,
                        }
                    ],
                }
            )
        )

        result = offline_bootstrap(tmpdir)

        assert "catalog" in result
        assert "profile" in result
        assert len(result["catalog"].entries) == 1
        assert len(result["profile"].entries) == 1
