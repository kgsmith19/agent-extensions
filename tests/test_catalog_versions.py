import pytest
from agent_extensions.schemas.catalog_schema import (
    Catalog,
    CatalogEntry,
    find_conflicting_versions,
)


def _entry(semantic_id="capability.search", version="1.0.0", **kw):
    base = dict(
        semantic_id=semantic_id,
        name="Web Search",
        provider="anthropic",
        version=version,
        source_pin="sha256:abc123...",
        license="MIT",
    )
    base.update(kw)
    return CatalogEntry(**base)


def test_find_conflicting_versions_flags_same_id_different_versions():
    """Protects version integrity; same semantic ID at two versions is a conflict."""
    conflicts = find_conflicting_versions([_entry(version="1.0.0"), _entry(version="2.0.0")])
    assert len(conflicts) == 1
    assert conflicts[0].semantic_id == "capability.search"
    assert sorted(conflicts[0].versions) == ["1.0.0", "2.0.0"]


def test_find_conflicting_versions_clean_when_single_version():
    """Positive control: one version per ID means no conflicts."""
    assert find_conflicting_versions([_entry(version="1.0.0")]) == []
    assert find_conflicting_versions([]) == []


def test_catalog_rejects_same_id_different_version_entries():
    """Protects catalog integrity; conflicting versions cannot coexist in one Catalog."""
    with pytest.raises(ValueError, match="conflicting versions"):
        Catalog(entries=[_entry(version="1.0.0"), _entry(version="2.0.0")])


def test_catalog_accepts_distinct_ids_same_version():
    """Positive control: same version string across different IDs is fine."""
    catalog = Catalog(entries=[_entry(semantic_id="capability.a"), _entry(semantic_id="capability.b")])
    assert len(catalog.entries) == 2


def test_get_by_id_returns_none_for_unknown():
    """Positive control: unknown IDs return None instead of raising."""
    catalog = Catalog(entries=[_entry()])
    assert catalog.get_by_id("capability.missing") is None
