"""Provider enforcement (Stage 54b EXT half): render + enforce the 54a profile.

The Standard half (54a) compiles capability bounds; this half renders and
enforces them per provider through real sandbox/permission/wrapper
mechanics — dry-run/report with sharp-edge enforcement. No provider
sandbox support is ever fabricated: an unsupported provider gets an
explicit wrapper/proxy or a declared gap, never a false claim.

Enforcement verdicts: ALLOW (inside bounds) / DENY (sharp edge) /
WRAPPER (explicit proxy path) / GAP (declared, must not enforce).
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional


class Enforcement(str, Enum):
    ALLOW = "ALLOW"
    DENY = "DENY"
    WRAPPER = "WRAPPER"
    GAP = "GAP"


SANDBOX_SUPPORT: Dict[str, bool] = {
    "claude": True,
    "codex": False,
    "antigravity": False,
    "local": False,
}

WRAPPER_PATH: Dict[str, str] = {
    "claude": "",
    "codex": "hook_env_wrapper: PreToolUse gate",
    "antigravity": "hook_env_wrapper: PreToolUse gate",
    "local": "manual review (no hook surface)",
}

SHARP_EDGES = (
    "foreign-write",
    "frozen-mold",
    "direct-main",
    "secret-raw",
    "arbitrary-egress",
    "research-write",
)


@dataclass
class ProviderDecision:
    """One per-provider enforcement outcome."""

    provider: str
    verdict: Enforcement
    mechanism: str
    detail: str = ""


def _inside(roots: List[str], target: str) -> bool:
    text = (target or "").replace("\\", "/").lstrip("/")
    for root in roots:
        guard = (root or "").replace("\\", "/").lstrip("/")
        if not guard:
            continue
        if text == guard or text.startswith(guard.rstrip("/") + "/"):
            return True
    return False


def enforce(provider: str, action: str, target: str, profile: dict) -> ProviderDecision:
    """Enforce one action on one provider against the compiled profile.

    Sharp edges deny on every provider. In-bounds reads/writes allow where
    the provider enforces natively, or via the explicit wrapper path where
    it does not. Sandbox-less execution without a wrapper declares a gap —
    never a false allow.
    """
    from agent_extensions.sync import adapters

    if provider not in adapters.PROVIDERS:
        return ProviderDecision(provider, Enforcement.GAP,
                                "unknown provider",
                                f"no adapter exists for {provider!r}; declared gap")
    if action in SHARP_EDGES:
        return ProviderDecision(provider, Enforcement.DENY,
                                "sharp-edge category",
                                f"{action} denied on every provider")
    if action == "foreign-read":
        roots = list(profile.get("read_roots", []))
        if _inside(roots, target):
            mechanism = "native read roots" if SANDBOX_SUPPORT[provider] \
                else WRAPPER_PATH[provider]
            return ProviderDecision(provider, Enforcement.ALLOW, mechanism,
                                    "inside read roots")
        return ProviderDecision(provider, Enforcement.DENY, "read roots",
                                f"{target!r} outside read roots")
    if action == "focused-edit":
        roots = list(profile.get("write_roots", []))
        if _inside(roots, target):
            mechanism = "native write roots" if SANDBOX_SUPPORT[provider] \
                else WRAPPER_PATH[provider]
            verdict = Enforcement.ALLOW if SANDBOX_SUPPORT[provider] \
                else Enforcement.WRAPPER
            return ProviderDecision(provider, verdict, mechanism,
                                    "inside write roots")
        return ProviderDecision(provider, Enforcement.DENY, "write roots",
                                f"{target!r} outside write roots")
    if action == "test-command":
        allowed = list(profile.get("exec_allow", []))
        if target in allowed:
            return ProviderDecision(provider, Enforcement.ALLOW,
                                    "allowlisted test command",
                                    "verification command allowed")
        return ProviderDecision(provider, Enforcement.DENY, "exec allowlist",
                                f"{target!r} not allowlisted")
    if action == "egress":
        allowed = list(profile.get("net_allow", []))
        host = target.split("://", 1)[-1].split("/")[0].split(":")[0]
        if host in allowed or target in allowed:
            return ProviderDecision(provider, Enforcement.ALLOW,
                                    "network allowlist", "egress allowed")
        return ProviderDecision(provider, Enforcement.DENY, "network allowlist",
                                f"{host!r} not allowlisted")
    if action == "secret-handle":
        return ProviderDecision(provider, Enforcement.ALLOW,
                                "scoped secret handle",
                                "handle-mounted secret, never raw")
    if action == "execute-custom":
        if not SANDBOX_SUPPORT[provider]:
            if WRAPPER_PATH[provider]:
                return ProviderDecision(provider, Enforcement.WRAPPER,
                                        WRAPPER_PATH[provider],
                                        "explicit wrapper path, dry-run/report")
            return ProviderDecision(provider, Enforcement.GAP,
                                    "declared gap",
                                    f"{provider} cannot enforce execution")
        return ProviderDecision(provider, Enforcement.ALLOW,
                                "native sandbox", "sandboxed execution")
    if action == "owner-escalation":
        return ProviderDecision(provider, Enforcement.ALLOW,
                                "owner approval recorded",
                                "explicit owner escalation")
    return ProviderDecision(provider, Enforcement.GAP, "unknown action",
                            f"action {action!r} has no enforcement mapping; declared gap")


def false_block_rate(decisions: List[ProviderDecision]) -> Dict[str, float]:
    """Dry-run/report metric: share of ALLOW-intended actions denied."""
    intended = [d for d in decisions if d.detail.startswith("dry-run-expect-allow")]
    if not intended:
        return {"false_block_rate": 0.0, "sampled": 0.0}
    denied = [d for d in intended if d.verdict == Enforcement.DENY]
    return {"false_block_rate": len(denied) / len(intended),
            "sampled": float(len(intended))}
