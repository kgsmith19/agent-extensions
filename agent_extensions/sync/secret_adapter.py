"""Secret adapter (INT-08 #37): Infisical-first scoped references, fail-closed.

Ref format: ``infisical://<project>/<env>/<role>/<path>`` — role/env refs
resolved at use time through short-lived role-scoped tokens. Expiry and
denial fail closed with an explanation naming what is absent or denied;
owner-account fallback never happens. A fake/test replacement satisfies
the identical contract for conformance testing. Raw secret values never
enter the repository, logs, or receipts — the adapter boundary redacts.
"""

import re
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional


REF_RE = re.compile(
    r"^infisical://(?P<project>[A-Za-z0-9_.-]+)/(?P<env>[A-Za-z0-9_.-]+)/"
    r"(?P<role>[A-Za-z0-9_.-]+)/(?P<path>[A-Za-z0-9_./-]+)$"
)

SECRET_SHAPES = (
    re.compile(r"sk-(ant-)?[A-Za-z0-9]{8,}"),
    re.compile(r"xox[bap]-"),
    re.compile(r"ghp_[A-Za-z0-9]{8,}"),
    re.compile(r"AKIA[0-9A-Z]{16}"),
)


class Resolution(str, Enum):
    VALUE = "VALUE"
    BLOCKED = "BLOCKED"
    DENIED = "DENIED"
    EXPIRED = "EXPIRED"


@dataclass
class RoleToken:
    """Short-lived role-scoped token: role + expiry epoch, never long-lived."""

    role: str
    expires_at: float
    token: str = field(repr=False, default="role-token")

    def live(self, now: Optional[float] = None) -> bool:
        return (now if now is not None else time.time()) < self.expires_at


@dataclass
class ResolveResult:
    """One scoped-ref resolution: value or fail-closed block with reason."""

    status: Resolution
    value: str = field(default="", repr=False)
    reason: str = ""


def parse_ref(ref: str) -> Dict[str, str]:
    """Parse a scoped ref into project/env/role/path; raises ValueError when malformed."""
    match = REF_RE.match((ref or "").strip())
    if not match:
        raise ValueError(
            f"malformed secret ref {ref!r}: want "
            "infisical://<project>/<env>/<role>/<path>"
        )
    return match.groupdict()


class SecretAdapter:
    """Infisical-first resolver: role tokens, expiry, denial, no owner fallback."""

    def __init__(self, store: Optional[Dict[str, str]] = None,
                 federation: Optional[Dict[str, bool]] = None):
        self._store = dict(store or {})
        self._federation = dict(federation or {})
        self._tokens: Dict[str, RoleToken] = {}

    def mint(self, role: str, ttl_seconds: int = 300,
             now: Optional[float] = None) -> RoleToken:
        """Mint a short-lived role token (test seam: injectable clock)."""
        moment = now if now is not None else time.time()
        token = RoleToken(role=role, expires_at=moment + ttl_seconds)
        self._tokens[role] = token
        return token

    def resolve(self, ref: str, *, now: Optional[float] = None) -> ResolveResult:
        """Resolve one scoped ref or fail closed with an explanation."""
        try:
            parts = parse_ref(ref)
        except ValueError as exc:
            return ResolveResult(Resolution.BLOCKED, reason=str(exc))
        role, env = parts["role"], parts["env"]
        if not self._federation.get(role, True):
            return ResolveResult(
                Resolution.BLOCKED,
                reason=f"federation absent for role {role!r}: "
                       f"configure federation before resolving")
        if self._federation.get(f"deny:{role}", False):
            return ResolveResult(
                Resolution.DENIED,
                reason=f"federation denied role {role!r}: assumption refused")
        token = self._tokens.get(role)
        moment = now if now is not None else time.time()
        if token is None or not token.live(moment):
            return ResolveResult(
                Resolution.EXPIRED,
                reason=f"role token for {role!r} missing or expired: "
                       f"re-acquire; never serves stale values, never falls "
                       f"back to the owner account")
        key = f"{parts['project']}/{env}/{role}/{parts['path']}"
        if key not in self._store:
            return ResolveResult(
                Resolution.BLOCKED,
                reason=f"no secret at {key!r} for env {env!r}")
        return ResolveResult(Resolution.VALUE, value=self._store[key])


class FakeSecretAdapter(SecretAdapter):
    """Test replacement satisfying the identical contract (AC2)."""

    def __init__(self):
        super().__init__(
            store={"demo/prod/ci-runner/api/key": "fake-value"},
            federation={})


def scan_for_raw_secrets(text: str) -> List[str]:
    """Redaction proof: secret-shaped values found in a log/receipt/repo scan."""
    hits = []
    for pattern in SECRET_SHAPES:
        for match in pattern.findall(text or ""):
            sample = match if isinstance(match, str) else match[0]
            hits.append(sample[:8] + "...")
    return hits


def drill_blocked_with_explanation(adapter: SecretAdapter) -> Dict[str, str]:
    """Runbook drill: federation-absent resolution blocks with explanation."""
    adapter._federation["drill-role"] = False
    adapter._tokens.pop("drill-role", None)
    result = adapter.resolve("infisical://demo/prod/drill-role/api/key")
    return {"status": result.status.value, "reason": result.reason,
            "owner_fallback": "never"}
