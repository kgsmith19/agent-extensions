import pytest
from agent_extensions.schemas.catalog_schema import Catalog, CatalogEntry
from agent_extensions.schemas.profile_schema import LifecycleState, Profile
from agent_extensions.sync.selection import (
    assert_profile_subset_of_catalog,
    build_profile,
    select_for_task,
)


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
            CatalogEntry(
                semantic_id="capability.math",
                name="Math",
                provider="anthropic",
                version="1.0.0",
                source_pin="sha256:def456...",
                license="MIT",
                disabled=True,
            ),
        ]
    )


def test_build_profile_rejects_unselected_body_loading():
    """Protects subset discipline; selected entries without requested IDs fail."""
    catalog = _catalog()
    profile = build_profile(catalog, ["capability.search"])
    with pytest.raises(ValueError, match="not in the selected set|unselected"):
        assert_profile_subset_of_catalog(
            catalog, profile, selected_ids=["capability.other"]
        )


def test_build_profile_skips_disabled_entries():
    """Protects deployment discipline; disabled catalog entries are never selected."""
    catalog = _catalog()
    with pytest.raises(ValueError, match="disabled"):
        build_profile(catalog, ["capability.math"])


def test_build_profile_rejects_degraded_selection():
    """Protects selection integrity; DEGRADED entries cannot be built into a profile."""
    catalog = _catalog()
    with pytest.raises(ValueError, match="DEGRADED|selectable"):
        build_profile(
            catalog, ["capability.search"], initial_state=LifecycleState.DEGRADED
        )


def test_select_for_task_returns_tiny_footprint():
    """Protects footprint separation; task selection is a small subset of supply."""
    catalog = _catalog()
    profile = select_for_task(catalog, ["capability.search"])
    assert isinstance(profile, Profile)
    assert [e.semantic_id for e in profile.entries] == ["capability.search"]
    assert all(e.state is LifecycleState.ENABLED for e in profile.entries)


def test_select_for_task_rejects_missing_ids():
    """Protects referential integrity; unknown IDs fail with repair guidance."""
    catalog = _catalog()
    with pytest.raises(ValueError, match="missing catalog entry|unknown"):
        select_for_task(catalog, ["capability.missing"])
