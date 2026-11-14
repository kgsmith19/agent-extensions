"""Owner Stand-In authority contract v1 (#109, canonical outcome #98).

This module validates policy metadata only.  It cannot grant a role, mint a
credential, or authorize an operation.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Annotated, Any, Literal

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    StringConstraints,
    model_validator,
)

SCHEMA_VERSION = "1.0.0"
JSON_SCHEMA_DIALECT = "https://json-schema.org/draft/2020-12/schema"
_SOURCE_PATHS = {"standard-lock": Path("standard.lock")}

Slug = Annotated[
    str,
    StringConstraints(
        min_length=2,
        max_length=80,
        pattern=r"^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$",
    ),
]
Digest = Annotated[
    str,
    StringConstraints(pattern=r"^sha256:[0-9a-f]{64}$"),
]


def _require_boolean(value: Any) -> bool:
    if type(value) is not bool:
        raise ValueError("boolean policy fields require an actual JSON boolean")
    return value


StrictTrue = Annotated[Literal[True], BeforeValidator(_require_boolean)]
StrictFalse = Annotated[Literal[False], BeforeValidator(_require_boolean)]

AuthorityClass = Literal[
    "browser-session-operator",
    "cloud-admin",
    "identity-admin",
    "repository-admin",
    "recovery-operator",
    "secrets-admin",
    "workspace-admin",
]


_SENSITIVE_PATTERNS = (
    re.compile(r"\bgh[pousr]_[A-Za-z0-9_]{12,}\b"),
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{12,}\b"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{16,}\b"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=-]{12,}", re.IGNORECASE),
    re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b"),
    re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"),
    re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),
)


def _strings(value: Any):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for key, item in value.items():
            yield str(key)
            yield from _strings(item)
    elif isinstance(value, (list, tuple, set)):
        for item in value:
            yield from _strings(item)


def assert_no_sensitive_values(value: Any) -> None:
    """Reject common secret and direct-PII shapes at any nesting depth."""
    for text in _strings(value):
        if any(pattern.search(text) for pattern in _SENSITIVE_PATTERNS):
            raise ValueError("sensitive value shape is forbidden in the authority contract")


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


class ProviderMapping(_StrictModel):
    provider: Slug
    principal_ref: Slug
    principal_kind: Literal[
        "human-delegated",
        "managed-browser-profile",
        "service-principal",
    ]
    authority_classes: list[AuthorityClass] = Field(min_length=1)

    @model_validator(mode="after")
    def unique_authority_classes(self):
        if len(self.authority_classes) != len(set(self.authority_classes)):
            raise ValueError("duplicate authority class in provider mapping")
        return self


class OrdinaryRole(_StrictModel):
    role: Slug
    authority_ceiling: Literal["bounded"]
    may_mint_owner_session: StrictFalse


class SourceRevision(_StrictModel):
    source: Slug
    digest: Digest


class SecretBoundary(_StrictModel):
    stored_material: Literal["identifiers-names-versions-digests-only"]
    target_process_delivery_only: StrictTrue
    model_context_allowed: StrictFalse
    ordinary_logs_allowed: StrictFalse
    cache_ttl_max_seconds: int = Field(ge=0, le=900)


class SessionPolicy(_StrictModel):
    credential_ttl_max_seconds: int = Field(gt=0, le=900)
    signed_capsule_required: StrictTrue
    session_bound: StrictTrue
    provider_read_back_required: StrictTrue
    unknown_harness_mode: Literal["restricted-degraded"]


class RecoveryPolicy(_StrictModel):
    independent_human_principal: Literal["physical-owner"]
    independent_from_standin: StrictTrue
    kill_switch_ref: Slug
    local_workspace_ref: Slug
    revoke_sla_seconds: int = Field(gt=0, le=900)


class RollbackPolicy(_StrictModel):
    disable_role_ref: Slug
    revoke_sessions_ref: Slug
    restore_scoped_roles: StrictTrue
    provider_recovery_ref: Slug


class OwnerStandInContract(_StrictModel):
    """Secret-free policy contract; validation never confers authority."""

    model_config = ConfigDict(
        extra="forbid",
        strict=True,
        frozen=True,
        json_schema_extra={"$schema": JSON_SCHEMA_DIALECT},
    )

    schema_version: Literal["1.0.0"]
    logical_identity: Literal["owner-standin"]
    authority_level: Literal["owner-equivalent"]
    canonical_outcome: Literal["kgsmith19/agent-extensions#98"]
    grants_live_authority: StrictFalse
    provider_mappings: list[ProviderMapping] = Field(min_length=1)
    ordinary_roles: list[OrdinaryRole] = Field(min_length=1)
    source_revisions: list[SourceRevision] = Field(min_length=1)
    secret_boundary: SecretBoundary
    session_policy: SessionPolicy
    recovery: RecoveryPolicy
    rollback: RollbackPolicy

    @model_validator(mode="after")
    def semantic_boundary(self):
        providers = [mapping.provider.casefold() for mapping in self.provider_mappings]
        if len(providers) != len(set(providers)):
            raise ValueError("duplicate provider mapping")
        roles = [role.role.casefold() for role in self.ordinary_roles]
        if len(roles) != len(set(roles)):
            raise ValueError("duplicate ordinary role")
        if "owner-standin" in roles:
            raise ValueError("owner-standin cannot be declared as an ordinary role")
        sources = [source.source.casefold() for source in self.source_revisions]
        if len(sources) != len(set(sources)):
            raise ValueError("duplicate source revision")
        assert_no_sensitive_values(self.model_dump(mode="json"))
        return self


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate JSON key is forbidden in the authority contract")
        value[key] = item
    return value


def load_contract(path: str | Path) -> OwnerStandInContract:
    """Load and fail closed on malformed, unknown, or unsafe contract data."""
    raw = json.loads(
        Path(path).read_text(encoding="utf-8"), object_pairs_hook=_unique_object
    )
    return OwnerStandInContract.model_validate(raw)


def canonical_json(contract: OwnerStandInContract) -> str:
    """Return deterministic UTF-8 JSON bytes represented as text."""
    return json.dumps(
        contract.model_dump(mode="json"),
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )


def contract_digest(contract: OwnerStandInContract) -> str:
    """Bind future capsules and receipts to one exact policy instance."""
    return "sha256:" + hashlib.sha256(canonical_json(contract).encode("utf-8")).hexdigest()


def verify_source_revisions(
    contract: OwnerStandInContract, root: str | Path
) -> list[str]:
    """Read back contract-bound local sources without exposing their content."""
    base = Path(root)
    problems: list[str] = []
    for revision in contract.source_revisions:
        relative = _SOURCE_PATHS.get(revision.source)
        if relative is None:
            problems.append(f"{revision.source}: unsupported source")
            continue
        try:
            actual = "sha256:" + hashlib.sha256((base / relative).read_bytes()).hexdigest()
        except OSError:
            problems.append(f"{revision.source}: source unavailable")
            continue
        if actual != revision.digest:
            problems.append(f"{revision.source}: digest mismatch")
    return problems
