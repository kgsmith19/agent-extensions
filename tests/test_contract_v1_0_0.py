import pytest
from agent_extensions.schemas.contract_v1_0_0 import (
    ContractBinding,
    ContractValidator,
    validate_binding,
)

STANDARD_SHA = "ad3c6c5f1512f4406087b499ca4c37e7c39dc1ea"


def test_contract_binding_requires_standard_sha():
    """Protects synchronization; catches binding without merged Standard SHA."""
    with pytest.raises(ValueError, match="standard_merged_sha"):
        ContractBinding(
            contract_version="1.0.0",
            standard_merged_sha="",  # Empty SHA
            catalog_schema_version="1.0.0",
            profile_schema_version="1.0.0",
            timestamp="2026-09-18T06:30:00Z",
        )


def test_contract_binding_accepts_valid_sha():
    """Positive control: valid binding accepted."""
    binding = ContractBinding(
        contract_version="1.0.0",
        standard_merged_sha=STANDARD_SHA,
        catalog_schema_version="1.0.0",
        profile_schema_version="1.0.0",
        timestamp="2026-09-18T06:30:00Z",
    )
    assert binding.standard_merged_sha == STANDARD_SHA


def test_contract_validator_checks_version_match():
    """Protects version alignment; catches mismatched contract versions."""
    binding = ContractBinding(
        contract_version="1.0.0",
        standard_merged_sha=STANDARD_SHA,
        catalog_schema_version="1.0.0",
        profile_schema_version="1.0.0",
        timestamp="2026-09-18T06:30:00Z",
    )

    assert ContractValidator.validate_version_match(binding, "1.0.0")
    assert not ContractValidator.validate_version_match(binding, "1.1.0")


def test_contract_validator_checks_sha_registry():
    """Protects pin integrity; binding SHA must match the registered Standard SHA."""
    binding = ContractBinding(standard_merged_sha=STANDARD_SHA)
    assert ContractValidator.validate_standard_sha_against_registry(binding, STANDARD_SHA)
    assert not ContractValidator.validate_standard_sha_against_registry(binding, "0" * 40)


def test_contract_validator_checks_compatibility():
    """Protects binding integrity; flags deprecated contracts."""
    binding = ContractBinding(
        contract_version="1.0.0",
        standard_merged_sha=STANDARD_SHA,
        binding_status="ACTIVE",
    )

    compatible, error = ContractValidator.check_compatibility(binding, [STANDARD_SHA])
    assert compatible
    assert error is None

    # Test with deprecated binding
    binding.binding_status = "DEPRECATED"
    compatible, error = ContractValidator.check_compatibility(binding, [])
    assert not compatible
    assert "deprecated" in error.lower()


def test_contract_validator_flags_unknown_sha():
    """Protects synchronization; unknown Standard SHAs are rejected."""
    binding = ContractBinding(standard_merged_sha=STANDARD_SHA)
    compatible, error = ContractValidator.check_compatibility(binding, ["0" * 40])
    assert not compatible
    assert "not found in registry" in error


def test_validate_binding_parses_raw_dict():
    """Positive control: raw binding dicts parse into ContractBinding."""
    binding = validate_binding(
        {
            "contract_version": "1.0.0",
            "standard_merged_sha": STANDARD_SHA,
        }
    )
    assert isinstance(binding, ContractBinding)
    assert binding.catalog_schema_version == "1.0.0"
