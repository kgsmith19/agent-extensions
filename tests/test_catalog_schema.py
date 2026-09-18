import pytest
from agent_extensions.schemas.catalog_schema import Catalog, CatalogEntry, validate_catalog

def test_catalog_rejects_duplicate_capability_id():
    """Protects uniqueness; catches two entries with same semantic ID and version."""
    entries = [
        CatalogEntry(
            semantic_id="capability.search",
            name="Web Search v1",
            provider="anthropic",
            version="1.0.0",
            source_pin="sha256:abc123...",
            license="MIT",
        ),
        CatalogEntry(
            semantic_id="capability.search",  # Duplicate ID, same version
            name="Web Search v1 (copy)",
            provider="openai",
            version="1.0.0",
            source_pin="sha256:def456...",
            license="Apache-2.0",
        ),
    ]

    with pytest.raises(ValueError, match="duplicate.*semantic_id"):
        Catalog(entries=entries)


def test_catalog_entry_requires_valid_semantic_id():
    """Protects semantic ID format; rejects entries without 'capability.' prefix."""
    with pytest.raises(ValueError, match="must start with"):
        CatalogEntry(
            semantic_id="search",  # Missing 'capability.' prefix
            name="Web Search",
            provider="anthropic",
            version="1.0.0",
            source_pin="sha256:abc123...",
            license="MIT",
        )

def test_catalog_entry_requires_source_pin():
    """Protects reproducibility; rejects entries without valid pin."""
    with pytest.raises(ValueError, match="source_pin"):
        CatalogEntry(
            semantic_id="capability.search",
            name="Web Search",
            provider="anthropic",
            version="1.0.0",
            source_pin="",  # Empty pin
            license="MIT",
        )

def test_catalog_get_by_id_returns_entry():
    """Positive control: retrieval works."""
    catalog = Catalog(
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
    result = catalog.get_by_id("capability.search")
    assert result is not None
    assert result.name == "Web Search"
