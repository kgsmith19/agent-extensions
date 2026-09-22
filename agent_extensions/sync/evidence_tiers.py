"""Evidence tiers (T17/INT-02): structural/translation/runtime never collapse.

Three reported tiers with strict promotion rules — execution is required
for runtime; names are never execution:

- structural: manifest/schema shapes only (no execution implied).
- translation: adapter output rendering (no execution implied).
- runtime: executed conformance only (executed evidence required).

Plus: passing negatives stay green without marking live targets
unhealthy; authenticated-but-unrun is unverified/degraded (never failed,
never healthy); every verdict carries adapter + harness versions
(versionless verdicts are rejected); a broken-manifest fixture is
quarantined to its own scope and never enters health aggregation.
"""

import json
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Dict, List, Optional


class EvidenceTier(str, Enum):
    STRUCTURAL = "structural"
    TRANSLATION = "translation"
    RUNTIME = "runtime"


class Health(str, Enum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    FAILED = "failed"
    UNVERIFIED = "unverified"


@dataclass
class TieredVerdict:
    """One verdict at exactly one tier — tiers never collapse."""

    capability: str
    tier: EvidenceTier
    health: Health
    adapter_version: str
    harness_version: str
    executed: bool
    detail: str = ""


def make_verdict(capability: str, tier: EvidenceTier, health: Health,
                 *, adapter_version: str = "", harness_version: str = "",
                 executed: bool = False, detail: str = "") -> TieredVerdict:
    """Create one tiered verdict (validation happens in validate_verdict)."""
    return TieredVerdict(
        capability=capability, tier=tier, health=health,
        adapter_version=adapter_version, harness_version=harness_version,
        executed=executed, detail=detail,
    )


def validate_verdict(verdict: TieredVerdict) -> List[str]:
    """Return repair strings; empty means valid. Enforces AC2/AC4/AC6."""
    problems = []
    if not verdict.adapter_version.strip() or not verdict.harness_version.strip():
        problems.append(f"versionless verdict rejected for {verdict.capability!r}: "
                        "every verdict carries adapter + harness versions")
    if verdict.tier == EvidenceTier.RUNTIME and not verdict.executed:
        problems.append(f"substring is not runtime for {verdict.capability!r}: "
                        "only executed conformance counts as runtime evidence")
    return problems


def negative_stays_green(passed: bool) -> Health:
    """AC1: a correctly-passing negative stays green; live targets unaffected."""
    return Health.HEALTHY if passed else Health.FAILED


def auth_unrun_health() -> Health:
    """AC3: authenticated-but-unrun is unverified/degraded — never failed, never healthy."""
    return Health.DEGRADED


def aggregate_health(verdicts: List[TieredVerdict]) -> Dict[str, str]:
    """Aggregate live-target health; quarantined scopes excluded by the caller.

    Any FAILED verdict fails the aggregate; else any DEGRADED degrades it;
    else healthy. UNVERIFIED alone never fails — it degrades only when no
    FAILED/DEGRADED is present... it stays unverified, never failed/healthy.
    """
    if any(v.health == Health.FAILED for v in verdicts):
        return {"health": Health.FAILED.value}
    if any(v.health in (Health.DEGRADED, Health.UNVERIFIED) for v in verdicts):
        degraded = [v for v in verdicts if v.health == Health.DEGRADED]
        unverified = [v for v in verdicts if v.health == Health.UNVERIFIED]
        if degraded:
            return {"health": Health.DEGRADED.value}
        return {"health": Health.UNVERIFIED.value}
    return {"health": Health.HEALTHY.value}


QUARANTINE_SCOPE = "broken-manifest-fixture"


def parse_manifest_quarantined(manifest_text: str) -> tuple[bool, str, Optional[dict]]:
    """Parse one manifest; malformed input is quarantined to its own scope.

    Returns (ok, scope, payload): failures report scope=QUARANTINE_SCOPE and
    payload None so the caller can exclude them from health aggregation.
    """
    try:
        payload = json.loads(manifest_text)
    except (ValueError, TypeError) as exc:
        return False, QUARANTINE_SCOPE, None
    if not isinstance(payload, dict):
        return False, QUARANTINE_SCOPE, None
    return True, "live", payload
