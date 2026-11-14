"""Executable policy boundary for Owner Stand-In AC1 (#109, parent #98)."""

from __future__ import annotations

import copy
import json
import re
from pathlib import Path

import pytest
from pydantic import ValidationError

from agent_extensions.schemas.owner_standin_v1 import (
    OwnerStandInContract,
    assert_no_sensitive_values,
    canonical_json,
    contract_digest,
    load_contract,
    verify_source_revisions,
)

ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = ROOT / "agent_extensions" / "contracts" / "owner_standin_v1.json"
SCHEMA_PATH = ROOT / "agent_extensions" / "schemas" / "owner_standin_v1.schema.json"
THREAT_MODEL_PATH = ROOT / "docs" / "OWNER_STANDIN_THREAT_MODEL.md"


def _raw() -> dict:
    return json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))


def _reject(mutator, match: str | None = None) -> None:
    value = copy.deepcopy(_raw())
    mutator(value)
    with pytest.raises(ValidationError, match=match):
        OwnerStandInContract.model_validate(value)


def test_canonical_contract_is_secret_free_non_authorizing_and_digestible():
    contract = load_contract(CONTRACT_PATH)

    assert contract.schema_version == "1.0.0"
    assert contract.logical_identity == "owner-standin"
    assert contract.authority_level == "owner-equivalent"
    assert contract.canonical_outcome == "kgsmith19/agent-extensions#98"
    assert contract.grants_live_authority is False
    assert {item.provider for item in contract.provider_mappings} == {
        "browser",
        "cloud",
        "github",
        "google",
        "infisical",
    }
    assert "@" not in canonical_json(contract)
    assert re.fullmatch(r"sha256:[0-9a-f]{64}", contract_digest(contract))


def test_checked_schema_is_exact_model_schema_and_strict_draft_2020_12():
    checked = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))

    assert checked == OwnerStandInContract.model_json_schema()
    assert checked["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    assert checked["additionalProperties"] is False


def test_source_revision_read_back_detects_exact_file_drift(tmp_path):
    contract = load_contract(CONTRACT_PATH)

    assert verify_source_revisions(contract, ROOT) == []
    (tmp_path / "standard.lock").write_text("changed\n", encoding="utf-8")
    assert verify_source_revisions(contract, tmp_path) == [
        "standard-lock: digest mismatch"
    ]


def test_validated_contract_is_immutable():
    contract = load_contract(CONTRACT_PATH)

    with pytest.raises(ValidationError, match="frozen"):
        contract.grants_live_authority = True


def test_loader_rejects_duplicate_json_keys_before_model_validation(tmp_path):
    path = tmp_path / "duplicate.json"
    path.write_text(
        '{"schema_version":"1.0.0","schema_version":"1.0.0"}',
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="duplicate JSON key"):
        load_contract(path)


@pytest.mark.parametrize(
    "mutator, match",
    [
        (lambda value: value.update(schema_version="2.0.0"), "schema_version"),
        (lambda value: value.update(unexpected=True), "extra_forbidden"),
        (lambda value: value.update(ordinary_roles=[]), "ordinary_roles"),
        (lambda value: value.pop("recovery"), "recovery"),
        (
            lambda value: value["ordinary_roles"][0].update(authority_ceiling="owner-equivalent"),
            "authority_ceiling",
        ),
        (
            lambda value: value["source_revisions"][0].update(
                digest="sha256:" + "A" * 64
            ),
            "digest",
        ),
        (
            lambda value: value["provider_mappings"].append(
                copy.deepcopy(value["provider_mappings"][0])
            ),
            "duplicate provider",
        ),
        (
            lambda value: value["provider_mappings"].append(
                {
                    **copy.deepcopy(value["provider_mappings"][0]),
                    "provider": value["provider_mappings"][0]["provider"].upper(),
                }
            ),
            "provider",
        ),
        (
            lambda value: value["provider_mappings"][0].update(
                principal_ref="owner\nadmin"
            ),
            "principal_ref",
        ),
        (
            lambda value: value["provider_mappings"][0].update(
                principal_ref="owner@example.test"
            ),
            "principal_ref",
        ),
    ],
)
def test_contract_fails_closed(mutator, match):
    _reject(mutator, match)


@pytest.mark.parametrize(
    "value",
    [
        {"future": [{"nested": "ghp_examplecredential123456"}]},
        {"future": "sk-examplecredential1234567890"},
        {"future": "owner@example.test"},
        {"future": "-----BEGIN PRIVATE KEY-----"},
    ],
)
def test_recursive_boundary_rejects_sensitive_values_in_future_shapes(value):
    with pytest.raises(ValueError, match="sensitive value"):
        assert_no_sensitive_values(value)


@pytest.mark.parametrize(
    "path, numeric",
    [
        (("grants_live_authority",), 0),
        (("ordinary_roles", 0, "may_mint_owner_session"), 0),
        (("secret_boundary", "target_process_delivery_only"), 1),
        (("secret_boundary", "model_context_allowed"), 0),
        (("secret_boundary", "ordinary_logs_allowed"), 0),
        (("session_policy", "signed_capsule_required"), 1),
        (("session_policy", "session_bound"), 1),
        (("session_policy", "provider_read_back_required"), 1),
        (("recovery", "independent_from_standin"), 1),
        (("rollback", "restore_scoped_roles"), 1),
    ],
)
def test_boolean_policy_fields_reject_integer_lookalikes(path, numeric):
    value = _raw()
    target = value
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = numeric

    with pytest.raises(ValidationError, match="boolean"):
        OwnerStandInContract.model_validate(value)


def test_threat_model_covers_required_failures_and_non_authorizing_boundary():
    text = THREAT_MODEL_PATH.read_text(encoding="utf-8").lower()

    for phrase in (
        "replay",
        "forgery",
        "confused deputy",
        "credential exfiltration",
        "poisoned bootstrap",
        "provider compromise",
        "browser-session theft",
        "cloud outage",
        "recovery loss",
        "kyle takeover",
        "rollback",
        "grants no authority",
    ):
        assert phrase in text


def test_readme_routes_to_the_only_canonical_outcome():
    text = (ROOT / "README.md").read_text(encoding="utf-8")

    assert "agent-extensions#98" in text
    assert "OWNER_STANDIN_THREAT_MODEL.md" in text
    assert "grants no live authority" in text
