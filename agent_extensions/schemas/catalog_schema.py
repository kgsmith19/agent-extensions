from typing import List, Dict, Optional
from pydantic import BaseModel, Field, field_validator

class CatalogEntry(BaseModel):
    """One extension in the catalog."""
    semantic_id: str = Field(..., description="Semantic capability ID from Standard registry")
    name: str = Field(..., description="Human-readable extension name")
    provider: str = Field(..., description="Provider identifier (anthropic, openai, gemini, etc.)")
    version: str = Field(..., description="Extension version (semver)")
    source_pin: str = Field(..., description="Git SHA or artifact hash for exact reproducibility")
    license: str = Field(..., description="SPDX license identifier")
    description: Optional[str] = Field(None, description="Optional long-form description")
    disabled: bool = Field(False, description="Whether this entry is disabled in current deployment")

    @field_validator("semantic_id")
    @classmethod
    def validate_semantic_id(cls, v):
        if not v or not v.startswith("capability."):
            raise ValueError("semantic_id must start with 'capability.'")
        return v

    @field_validator("source_pin")
    @classmethod
    def validate_source_pin(cls, v):
        if not v or len(v) < 8:
            raise ValueError("source_pin must be a valid git SHA or hash")
        return v


class Catalog(BaseModel):
    """The full extension catalog."""
    entries: List[CatalogEntry] = Field(default_factory=list, description="All catalog entries")
    version: str = Field(default="1.0.0", description="Catalog schema version")

    @field_validator("entries")
    @classmethod
    def validate_no_duplicate_ids(cls, entries):
        ids = [e.semantic_id for e in entries]
        if len(ids) != len(set(ids)):
            duplicates = [id for id in ids if ids.count(id) > 1]
            raise ValueError(f"duplicate semantic_id entries: {set(duplicates)}")
        return entries

    def get_by_id(self, semantic_id: str) -> Optional[CatalogEntry]:
        """Retrieve a catalog entry by semantic ID."""
        for entry in self.entries:
            if entry.semantic_id == semantic_id:
                return entry
        return None


def validate_catalog(data: Dict) -> Catalog:
    """Validate and parse raw catalog data."""
    return Catalog(**data)
