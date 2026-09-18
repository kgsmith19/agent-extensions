"""Contract binding schema with version/SHA validation."""

from datetime import datetime, timezone
from typing import Optional
from pydantic import BaseModel, Field, field_validator


class ContractBinding(BaseModel):
    """Version binding between Standard and agent-extensions sibling contracts."""

    contract_version: str = Field(default="1.0.0", description="Contract version (semver)")
    standard_merged_sha: str = Field(
        ..., description="Exact merged SHA of agent-engineering-standard#113 (Stage 15a)"
    )
    catalog_schema_version: str = Field(default="1.0.0", description="Catalog schema version")
    profile_schema_version: str = Field(default="1.0.0", description="Profile schema version")
    license_metadata_schema_version: str = Field(
        default="1.0.0", description="License metadata schema version"
    )
    timestamp: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"))
    binding_status: str = Field(default="ACTIVE", description="ACTIVE, DEPRECATED, or SUPERSEDED")

    @field_validator("standard_merged_sha")
    @classmethod
    def validate_standard_sha(cls, v):
        if not v or len(v) < 40:
            raise ValueError("standard_merged_sha must be a valid git SHA (40+ characters)")
        return v

    @field_validator("contract_version")
    @classmethod
    def validate_version(cls, v):
        if not v or not all(part.isdigit() for part in v.split(".")):
            raise ValueError("contract_version must be semver (e.g., 1.0.0)")
        return v


class ContractValidator:
    """Validates contract binding and compatibility."""

    @staticmethod
    def validate_version_match(
        binding: ContractBinding,
        standard_contract_version: str,
    ) -> bool:
        """Ensure agent-extensions contract version matches Standard version."""
        return binding.contract_version == standard_contract_version

    @staticmethod
    def validate_standard_sha_against_registry(
        binding: ContractBinding,
        registered_standard_sha: str,
    ) -> bool:
        """Verify binding references a registered Standard contract SHA."""
        return binding.standard_merged_sha == registered_standard_sha

    @staticmethod
    def check_compatibility(
        binding: ContractBinding,
        known_standard_contract_shas: list,
    ) -> tuple[bool, Optional[str]]:
        """Check if binding is compatible with known Standard contracts."""
        if binding.binding_status == "DEPRECATED":
            return False, "Contract binding is deprecated"
        if binding.binding_status == "SUPERSEDED":
            return False, "Contract binding is superseded"
        if binding.standard_merged_sha not in known_standard_contract_shas:
            return False, f"Standard SHA {binding.standard_merged_sha} not found in registry"
        return True, None


def validate_binding(data: dict) -> ContractBinding:
    """Parse and validate raw binding data."""
    return ContractBinding(**data)
