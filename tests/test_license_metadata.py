import pytest
from agent_extensions.schemas.license_metadata import LicenseMetadata, ProvenanceRecord, validate_spdx_license


def test_license_metadata_rejects_invalid_spdx():
    """Protects license compliance; rejects non-SPDX identifiers."""
    with pytest.raises(ValueError, match="not a valid SPDX"):
        LicenseMetadata(
            semantic_id="capability.search",
            spdx_license="Some-Custom-License",  # Invalid SPDX
            provenance=ProvenanceRecord(
                source_url="https://github.com/...",
                source_commit="abc123...",
            ),
        )


def test_license_metadata_accepts_common_spdx():
    """Positive control: common SPDX identifiers accepted."""
    for spdx_id in ["MIT", "Apache-2.0", "GPL-3.0-only", "BSD-3-Clause"]:
        meta = LicenseMetadata(
            semantic_id="capability.search",
            spdx_license=spdx_id,
            provenance=ProvenanceRecord(
                source_url="https://github.com/...",
                source_commit="abc123...",
            ),
        )
        assert meta.spdx_license == spdx_id


def test_license_metadata_preserves_existing_pins():
    """Protects existing extension data; migrating catalog preserves all pins."""
    # Simulate migrating from current marketplace declarations
    old_entries = [
        {
            "semantic_id": "capability.search",
            "name": "Web Search",
            "spdx_license": "MIT",
            "source_url": "https://github.com/example/search",
            "source_commit": "abc123def456789abc123def456789abc12345",
        }
    ]
    
    # Conversion should preserve all data
    metadata = LicenseMetadata(
        semantic_id=old_entries[0]["semantic_id"],
        spdx_license=old_entries[0]["spdx_license"],
        provenance=ProvenanceRecord(
            source_url=old_entries[0]["source_url"],
            source_commit=old_entries[0]["source_commit"],
        ),
    )
    
    assert metadata.semantic_id == old_entries[0]["semantic_id"]
    assert metadata.provenance.source_commit == old_entries[0]["source_commit"]
    assert metadata.provenance.source_url == old_entries[0]["source_url"]
    assert metadata.spdx_license == old_entries[0]["spdx_license"]
