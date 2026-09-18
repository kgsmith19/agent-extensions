"""License metadata and provenance tracking schema."""

from typing import Optional, Set
from pydantic import BaseModel, Field, validator


# Common SPDX license identifiers (simplified list)
COMMON_SPDX_LICENSES = {
    "MIT",
    "Apache-2.0",
    "GPL-2.0-only",
    "GPL-3.0-only",
    "BSD-2-Clause",
    "BSD-3-Clause",
    "ISC",
    "MPL-2.0",
    "LGPL-2.1-only",
    "LGPL-3.0-only",
    "AGPL-3.0-only",
    "Proprietary",
}


def validate_spdx_license(spdx_id: str) -> bool:
    """Check if a license ID is a valid SPDX identifier (simplified check).
    
    Args:
        spdx_id: SPDX license identifier
        
    Returns:
        True if valid SPDX license or composite license (contains "+")
    """
    return spdx_id in COMMON_SPDX_LICENSES or "+" in spdx_id


class ProvenanceRecord(BaseModel):
    """Source and commit information for an extension.
    
    Tracks exact source location and revision for reproducibility.
    """
    source_url: str = Field(..., description="Git repository URL")
    source_commit: str = Field(..., description="Exact commit SHA")
    build_timestamp: Optional[str] = Field(None, description="Build/publish timestamp")

    @validator("source_commit")
    def validate_commit_sha(cls, v):
        """Ensure commit SHA is valid format."""
        if not v or len(v) < 8:
            raise ValueError("source_commit must be a valid git SHA")
        return v


class LicenseMetadata(BaseModel):
    """License and provenance tracking for an extension.
    
    Combines SPDX license compliance with exact source tracking.
    """
    semantic_id: str = Field(..., description="Semantic capability ID")
    spdx_license: str = Field(..., description="SPDX license identifier")
    provenance: ProvenanceRecord = Field(
        ...,
        description="Source and commit information",
    )
    additional_licenses: Optional[str] = Field(None, description="Additional license info or path")

    @validator("spdx_license")
    def validate_spdx(cls, v):
        """Ensure SPDX license is valid."""
        if not validate_spdx_license(v):
            raise ValueError(f"{v} is not a valid SPDX license identifier")
        return v

    @validator("semantic_id")
    def validate_semantic_id(cls, v):
        """Ensure semantic ID has required format."""
        if not v.startswith("capability."):
            raise ValueError("semantic_id must start with 'capability.'")
        return v
