from pathlib import Path

import pytest
from agent_extensions.sync.router import (
    PHASE_SKILLS,
    PROCESS_PLUS_DOMAIN_BUDGET,
    route_phase,
    router_index_tokens,
)

REPO = Path(__file__).resolve().parents[1]


def test_only_router_index_at_start():
    """Protects context; start/resume loads descriptors (~hundreds of tokens), not bodies."""
    index_cost = router_index_tokens(REPO)
    assert index_cost < 500, index_cost
    from agent_extensions.sync.discovery import index_repo

    body_cost = sum(d.body_tokens_est for d in index_repo(REPO))
    assert index_cost < body_cost // 10


def test_approved_spec_skips_brainstorming():
    """Protects flow; an approved Spec never re-triggers duplicate brainstorming."""
    decision = route_phase("brainstorming", REPO, approved_spec=True)
    assert decision.bodies_resolved == 0
    assert "not re-triggered" in decision.detail


def test_tdd_and_debugging_route():
    """Protects correct-route accuracy on the frozen phase set."""
    for phase in ("tdd", "debugging", "planning"):
        decision = route_phase(phase, REPO)
        assert decision.bodies_resolved >= 1
        assert decision.body_tokens > 0
        assert not decision.fallback_used


def test_over_budget_rejected_not_truncated():
    """Protects the budget; 3-body phases fail loudly instead of silently dropping one."""
    PHASE_SKILLS["overstuffed"] = ["skill-creator", "canvas-design", "web-artifacts-builder"]
    try:
        with pytest.raises(ValueError, match="over the.*budget"):
            route_phase("overstuffed", REPO)
    finally:
        del PHASE_SKILLS["overstuffed"]
    assert PROCESS_PLUS_DOMAIN_BUDGET == 2


def test_unselected_candidates_never_resolved():
    """Protects JIT; routing tdd resolves only tdd bodies, nothing else."""
    decision = route_phase("tdd", REPO)
    assert decision.selected == ["capability.skill-creator"]
    assert "capability.canvas-design" not in decision.selected


def test_provider_without_superpowers_falls_back():
    """Protects portability; unknown providers get the manual process, not nothing."""
    decision = route_phase("tdd", REPO, provider="kilo")
    assert decision.fallback_used
    assert "manual fallback" in decision.detail
    assert decision.bodies_resolved >= 1  # bodies still resolve; only the process is manual


def test_claude_codex_gemini_equivalence():
    """Protects cross-provider parity; all three route identically."""
    results = [route_phase("design", REPO, provider=p) for p in ("claude", "codex", "antigravity")]
    assert all(r.selected == results[0].selected for r in results)
    assert all(r.bodies_resolved == results[0].bodies_resolved for r in results)
    assert not any(r.fallback_used for r in results)


def test_unknown_phase_rejected():
    """Protects the contract; unmapped phases fail with the routable list."""
    with pytest.raises(ValueError, match="unknown phase"):
        route_phase("telepathy", REPO)


def test_skill_body_tokens_measured():
    """Protects measurement; every decision reports body tokens for the corpus."""
    decision = route_phase("design", REPO)
    assert decision.body_tokens > 1000
    assert decision.bodies_resolved == 2
