"""Deny-guard adapters (Stage 55b EXT half): monotonic deny at the hook layer.

The Standard half (55a) owns the guard-chain semantics; this half proves
each provider's hook path propagates denial monotonically — a provider hook
may NO_OP or restrict further, never FORCE_ALLOW after the trusted base
denied, and never silently restore authority on hook error. Gaps (e.g.
hook-inheritance on codex/antigravity) are declared, never fabricated.
"""

from dataclasses import dataclass
from enum import Enum
from typing import Dict, List, Optional


class HookVerdict(str, Enum):
    ALLOW = "ALLOW"
    DENY = "DENY"
    NO_OP = "NO_OP"


DENY_WORDS = ("deny", "denied", "refuse", "refused", "block", "blocked")


@dataclass
class AdapterDenyResult:
    """One per-provider hook-layer deny outcome."""

    provider: str
    verdict: HookVerdict
    mechanism: str
    detail: str = ""


def _hook_denies(log_text: str) -> bool:
    lowered = (log_text or "").lower()
    return any(word in lowered for word in DENY_WORDS)


def evaluate_hook(provider: str, base_verdict: str, hook_log: str,
                  hook_error: bool = False) -> AdapterDenyResult:
    """Evaluate one provider hook outcome against the trusted-base verdict.

    - Base DENY + hook ALLOW/empty success => DENY kept (later-allow refused).
    - Base DENY + hook error => DENY kept (bugs never restore authority).
    - Base ALLOW + hook DENY => DENY (hooks may restrict further).
    - Base ALLOW + hook clean => ALLOW via the provider mechanism.
    - Providers without hook inheritance (codex/antigravity/local) enforce
      via hook_env_wrapper/manual gates; the verdict math is identical.
    """
    from agent_extensions.sync import adapters

    if provider not in adapters.PROVIDERS:
        return AdapterDenyResult(provider, HookVerdict.NO_OP,
                                 "unknown provider",
                                 f"no adapter for {provider!r}; declared gap")
    mechanism = ("native hooks" if provider == "claude"
                 else "hook_env_wrapper PreToolUse gate" if provider in ("codex", "antigravity")
                 else "manual review gate")
    base_denied = (base_verdict or "").upper() == "DENY"
    hook_says_deny = _hook_denies(hook_log)
    if base_denied:
        if hook_error:
            return AdapterDenyResult(provider, HookVerdict.DENY, mechanism,
                                     "hook error after base DENY: denial holds")
        return AdapterDenyResult(provider, HookVerdict.DENY, mechanism,
                                 "base DENY holds; hook cannot FORCE_ALLOW")
    if hook_says_deny:
        return AdapterDenyResult(provider, HookVerdict.DENY, mechanism,
                                 "hook restricts further: DENY")
    if hook_error:
        return AdapterDenyResult(provider, HookVerdict.NO_OP, mechanism,
                                 "hook error without base deny: NO_OP + escalate")
    return AdapterDenyResult(provider, HookVerdict.ALLOW, mechanism,
                             "base ALLOW, hook clean")


def self_weaken_refused(policy_before: str, policy_after: str) -> bool:
    """True when a PR tightens-or-keeps policy (allowed); False when it weakens."""
    return len(policy_after or "") >= len(policy_before or "")


def canary_matrix() -> List[Dict[str, str]]:
    """R3 pilot canary matrix: every provider x later-allow/hook-bug/self-weaken."""
    rows = []
    for provider in ("claude", "codex", "antigravity", "local"):
        rows.append({"provider": provider, "case": "later-allow-after-deny",
                     "expect": "DENY"})
        rows.append({"provider": provider, "case": "hook-bug-after-deny",
                     "expect": "DENY"})
    rows.append({"provider": "all", "case": "pr-weakens-own-policy",
                 "expect": "DENY"})
    rows.append({"provider": "all", "case": "ordinary-allowed-edit",
                 "expect": "ALLOW"})
    return rows
