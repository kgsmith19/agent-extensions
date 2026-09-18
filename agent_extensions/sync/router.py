"""Controlled Superpowers router: index-only at start, bodies JIT per phase."""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

# Normal budget: one process skill body + one domain skill body. Never raised here.
PROCESS_PLUS_DOMAIN_BUDGET = 2

# Phase → skill routing (policy owned by Standard Stage 25a; this table is the
# extensions-side resolution map the router serves, not the policy itself).
PHASE_SKILLS: Dict[str, List[str]] = {
    "brainstorming": ["skill-creator"],
    "planning": ["skill-creator"],
    "tdd": ["skill-creator"],
    "debugging": ["skill-creator"],
    "design": ["canvas-design", "web-artifacts-builder"],
    "frontend": ["web-artifacts-builder"],
}

# Providers without Superpowers fall back to documented manual equivalents.
MANUAL_FALLBACK = {
    "skill-creator": "follow SKILL.md sections by hand; run scripts/ directly",
    "canvas-design": "follow SKILL.md; use canvas-fonts/ assets directly",
    "web-artifacts-builder": "run scripts/init-artifact.sh then build by hand",
}


@dataclass
class RouteDecision:
    """One routing outcome: descriptors consulted, bodies resolved, budget held."""

    phase: str
    provider: str
    selected: List[str] = field(default_factory=list)
    bodies_resolved: int = 0
    body_tokens: int = 0
    fallback_used: bool = False
    detail: str = ""


def route_phase(
    phase: str,
    repo_root: Path,
    provider: str = "claude",
    *,
    approved_spec: bool = False,
) -> RouteDecision:
    """Resolve bodies for a phase: index only, then at most budget bodies.

    - An approved Spec never re-triggers brainstorming (returns empty + note).
    - Over-budget selections are rejected, not truncated.
    - Providers without Superpowers get the manual-process fallback.

    Raises:
        ValueError: On unknown phases or budget violations.
    """
    from agent_extensions.sync.discovery import discovery_index, index_repo, resolve_body

    if phase not in PHASE_SKILLS:
        raise ValueError(
            f"unknown phase {phase!r}; routable phases: {sorted(PHASE_SKILLS)}"
        )
    if approved_spec and phase == "brainstorming":
        return RouteDecision(
            phase=phase, provider=provider,
            detail="approved Spec present; brainstorming not re-triggered",
        )
    wanted = PHASE_SKILLS[phase]
    if len(wanted) > PROCESS_PLUS_DOMAIN_BUDGET:
        raise ValueError(
            f"phase {phase!r} wants {len(wanted)} bodies, over the "
            f"one-process-plus-one-domain budget ({PROCESS_PLUS_DOMAIN_BUDGET}); "
            "narrow the phase mapping (Standard 25a owns the policy)"
        )
    descriptors = {d.name: d for d in index_repo(repo_root)}
    table = discovery_index(index_repo(repo_root))
    bodies, tokens = 0, 0
    for name in wanted:
        body = resolve_body(descriptors[name], repo_root)
        bodies += 1
        tokens += len(body.encode()) // 4
    fallback = provider not in ("claude", "codex", "antigravity", "local")
    return RouteDecision(
        phase=phase, provider=provider,
        selected=[f"capability.{n}" for n in wanted],
        bodies_resolved=bodies, body_tokens=tokens,
        fallback_used=fallback,
        detail=(f"manual fallback: {MANUAL_FALLBACK[wanted[0]]}" if fallback else "router/index only at start; bodies JIT"),
    )


def router_index_tokens(repo_root: Path) -> int:
    """Token cost of loading only the router index (descriptors, never bodies)."""
    from agent_extensions.sync.discovery import index_repo

    return sum(len(d.name) + len(d.description) for d in index_repo(repo_root)) // 4
