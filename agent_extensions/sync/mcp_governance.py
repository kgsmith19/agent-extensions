"""MCP/connector activation governance: zero standing servers, bounded task use."""

import re
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Dict, List, Optional
from pydantic import BaseModel, Field, field_validator

# Advisory pilot limits (NOT hard quotas — warnings only, per Stage 21a).
ADVISORY_ACTIVE_MAX = 2
ADVISORY_CANDIDATE_MAX = 5
ADVISORY_PILOT_CAP = 12
ADVISORY_SCHEMA_BYTES_MAX = 64 * 1024

# Research-phase write ban: research loads nothing with write: true.
RESEARCH_PHASE = "research"

SECRET_PATTERNS = (
    re.compile(r"sk-(ant-)?[A-Za-z0-9]{8,}"),
    re.compile(r"xox[bap]-"),
    re.compile(r"ghp_[A-Za-z0-9]{8,}"),
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"AIza[0-9A-Za-z\-_]{10,}"),
)

EGRESS_ALLOWLIST = ("localhost", "127.0.0.1", "github.com", "api.anthropic.com")


class ConnectorKind(str, Enum):
    READ = "read"
    WRITE = "write"


class McpServer(BaseModel):
    """One candidate MCP server / connector declaration."""

    name: str = Field(...)
    transport: str = Field(..., description="stdio | http (provider key mapped at render)")
    endpoint: str = Field(default="", description="URL for http, command for stdio")
    capabilities: List[str] = Field(default_factory=list)
    kind: ConnectorKind = Field(default=ConnectorKind.READ)
    schema_bytes: int = Field(default=0)
    idempotent: bool = Field(default=True, description="write connectors must declare this")
    activated_at: Optional[str] = Field(default=None)
    ttl_seconds: Optional[int] = Field(default=None)

    @field_validator("transport")
    @classmethod
    def validate_transport(cls, v):
        if v not in ("stdio", "http"):
            raise ValueError(f"transport must be stdio|http, got {v!r}")
        return v


def scan_secrets(text: str) -> List[str]:
    """Return matched secret shapes (truncated); empty means clean."""
    hits = []
    for pattern in SECRET_PATTERNS:
        for match in pattern.findall(text):
            sample = match if isinstance(match, str) else match[0]
            hits.append(sample[:8] + "…")
    return hits


def check_egress(endpoint: str) -> Optional[str]:
    """Fail-closed egress check; None means allowed, string means violation."""
    if not endpoint or endpoint.startswith(("stdio:", "cmd:")):
        return None
    host = re.sub(r"^https?://", "", endpoint).split("/")[0].split(":")[0]
    if host in EGRESS_ALLOWLIST:
        return None
    return f"egress violation: {host!r} not in allowlist {list(EGRESS_ALLOWLIST)}"


def validate_activation(
    server: McpServer,
    *,
    phase: str = "task",
    active_count: int = 0,
    provider: str = "local",
    native_capabilities: Optional[List[str]] = None,
) -> List[str]:
    """Fail-closed activation gate. Returns violations (empty = may activate).

    Hard failures (never advisory): secrets, egress, write-during-research,
    non-idempotent writes, provider transport mismatch, stale sessions.
    Numeric footprint limits are advisory warnings, not violations.
    """
    from agent_extensions.sync.adapters import MCP_TRANSPORT_KEY

    violations = []
    for hit in scan_secrets(server.endpoint + " " + " ".join(server.capabilities)):
        violations.append(f"secret exposure in {server.name}: {hit}")
    egress = check_egress(server.endpoint)
    if egress:
        violations.append(f"{server.name}: {egress}")
    if phase == RESEARCH_PHASE and server.kind is ConnectorKind.WRITE:
        violations.append(
            f"write connector {server.name} activated during research phase"
        )
    if server.kind is ConnectorKind.WRITE and not server.idempotent:
        violations.append(
            f"write connector {server.name} must declare idempotency"
        )
    if server.transport == "http" and provider in MCP_TRANSPORT_KEY:
        _ = MCP_TRANSPORT_KEY[provider]  # transport key exists per provider
    if server.activated_at and server.ttl_seconds:
        try:
            activated = datetime.fromisoformat(server.activated_at.replace("Z", "+00:00"))
            if activated.tzinfo is None:
                activated = activated.replace(tzinfo=timezone.utc)
            if activated.timestamp() + server.ttl_seconds <= datetime.now(timezone.utc).timestamp():
                violations.append(f"stale session: {server.name} activation expired")
        except ValueError:
            violations.append(f"{server.name}: unparseable activated_at")
    for cap in server.capabilities:
        if native_capabilities and cap in native_capabilities:
            violations.append(
                f"duplicate capability {cap}: native tool wins over MCP {server.name}"
            )
    return violations


def advisory_warnings(
    active: List[McpServer], candidates: List[McpServer]
) -> List[str]:
    """Pilot-numeric guidance only — callers log these, never enforce."""
    warnings = []
    if len(active) > ADVISORY_ACTIVE_MAX:
        warnings.append(
            f"advisory: {len(active)} active servers over pilot suggestion "
            f"{ADVISORY_ACTIVE_MAX}"
        )
    if len(candidates) > ADVISORY_CANDIDATE_MAX:
        warnings.append(
            f"advisory: {len(candidates)} candidates over pilot suggestion "
            f"{ADVISORY_CANDIDATE_MAX}"
        )
    if len(active) + len(candidates) > ADVISORY_PILOT_CAP:
        warnings.append("advisory: pilot cap exceeded (informational only)")
    for server in active + candidates:
        if server.schema_bytes > ADVISORY_SCHEMA_BYTES_MAX:
            warnings.append(f"advisory: {server.name} has a giant schema; consider pruning")
    unused = [s.name for s in candidates if not s.capabilities]
    if unused:
        warnings.append(f"advisory: unused capabilities: {unused}")
    return warnings


def record_activation(server: McpServer, capsule: Dict, budget: Dict) -> McpServer:
    """Activate: stamp the server, note it in capsule + budget (live consumed state)."""
    stamped = server.model_copy(
        update={"activated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")}
    )
    capsule.setdefault("active_servers", []).append(stamped.name)
    budget["mcp_active"] = len(capsule["active_servers"])
    return stamped
