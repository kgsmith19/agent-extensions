"""Extension locks: exact provenance ground truth for every rendered capability."""

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional, Union
from pydantic import BaseModel, Field, field_validator

LOCK_VERSION = "1.0.0"

_FLOATING_REF = re.compile(r"^(main|master|latest|HEAD|v?\d+\.x)$|^(v?\d+\.\d+)$")


def is_floating_ref(ref: str) -> bool:
    """A floating ref moves over time: branch names, latest/HEAD, partial versions."""
    if not ref or not isinstance(ref, str):
        return True
    ref = ref.strip()
    if _FLOATING_REF.match(ref):
        return True
    if ref.startswith(("sha256:", "sha512:")):
        return False
    # Full 40-hex git SHA is exact; anything shorter is floating/ambiguous.
    if re.fullmatch(r"[0-9a-fA-F]{40}", ref):
        return False
    return True


class ExtensionLock(BaseModel):
    """One immutable lock entry: exact source, license, digest, provenance."""

    identity: str = Field(..., description="Stable lock identity (e.g. capability.<slug>)")
    source_url: str = Field(..., description="Immutable upstream source (repo URL)")
    source_commit: str = Field(..., description="Exact full 40-hex upstream commit SHA")
    spdx_license: str = Field(..., description="SPDX license identifier")
    rendered_digest: str = Field(..., description="sha256:<hex> of the rendered artifact")
    upstream_kind: str = Field(
        default="pinned", description="pinned | live-marketplace (documented distinction)"
    )

    @field_validator("source_commit")
    @classmethod
    def validate_exact_commit(cls, v):
        if is_floating_ref(v) or not re.fullmatch(r"[0-9a-fA-F]{40}", v.strip()):
            raise ValueError(
                f"source_commit must be an exact full 40-hex SHA, got {v!r}; "
                "floating refs (branches, tags-in-motion) are rejected"
            )
        return v.strip()

    @field_validator("rendered_digest")
    @classmethod
    def validate_digest(cls, v):
        if not re.fullmatch(r"sha256:[0-9a-fA-F]{64}", (v or "").strip()):
            raise ValueError(
                f"rendered_digest must be sha256:<64-hex>, got {v!r}"
            )
        return v.strip()

    @field_validator("identity")
    @classmethod
    def validate_identity(cls, v):
        if not v or not v.strip():
            raise ValueError("identity must be non-empty")
        return v.strip()


class ExtensionsLock(BaseModel):
    """The lockfile: versioned set of ExtensionLock entries plus update history."""

    version: str = Field(default=LOCK_VERSION, description="Lockfile schema version")
    updated_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    )
    entries: List[ExtensionLock] = Field(default_factory=list)
    history: List[str] = Field(
        default_factory=list, description="Append-only rollback/update log lines"
    )

    @field_validator("entries")
    @classmethod
    def validate_unique_identities(cls, entries):
        ids = [e.identity for e in entries]
        if len(ids) != len(set(ids)):
            dupes = sorted({i for i in ids if ids.count(i) > 1})
            raise ValueError(f"duplicate lock identity entries: {dupes}")
        return entries

    def get(self, identity: str) -> Optional[ExtensionLock]:
        for entry in self.entries:
            if entry.identity == identity:
                return entry
        return None


def compute_digest(canonical_json: str) -> str:
    """sha256:<hex> of the canonical JSON string for a rendered artifact."""
    return "sha256:" + hashlib.sha256(canonical_json.encode()).hexdigest()


def verify_lockfile(lock: ExtensionsLock) -> List[str]:
    """Fail-closed verification: every entry exact, licensed, digest-shaped.

    Returns a list of problem strings; empty means the lockfile is sound.
    No network access: shape/exactness checks only (live source reads are
    the operator's explicit update step, documented in LOCK_UPDATE.md).
    """
    from agent_extensions.schemas.license_metadata import validate_spdx_license

    problems = []
    for entry in lock.entries:
        if is_floating_ref(entry.source_commit):
            problems.append(f"{entry.identity}: floating source_commit {entry.source_commit!r}")
        if not validate_spdx_license(entry.spdx_license):
            problems.append(
                f"{entry.identity}: missing or invalid license {entry.spdx_license!r}"
            )
    return problems


def read_lockfile(path: Union[str, Path]) -> ExtensionsLock:
    """Read and validate extensions.lock from disk."""
    data = json.loads(Path(path).read_text(encoding="utf-8", errors="replace"))
    return ExtensionsLock(**data)


def write_lockfile(lock: ExtensionsLock, path: Union[str, Path]) -> Path:
    """Write lockfile with history appended; returns the path."""
    dest = Path(path)
    dest.write_text(json.dumps(lock.model_dump(mode="json"), indent=2, sort_keys=True), encoding="utf-8")
    return dest
