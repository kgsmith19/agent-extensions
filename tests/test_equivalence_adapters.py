"""Stage 56b (EXT half #19): live per-provider equivalence canaries."""

from agent_extensions.sync.equivalence_adapters import (
    run_provider_canaries,
    unsupported_recorded,
)


def test_nested_conflict_all_providers():
    """Owner-direct outranks task-data on every provider."""
    for provider in ("claude", "codex", "antigravity", "local"):
        results = {r.case: r for r in run_provider_canaries(provider)}
        assert results["nested-conflict"].passed, provider


def test_agents_override_all_providers():
    """AGENTS routes survive porting on every provider."""
    for provider in ("claude", "codex", "antigravity", "local"):
        results = {r.case: r for r in run_provider_canaries(provider)}
        assert results["agents-override"].passed, provider


def test_injections_lose_all_providers():
    """Comment/filename/tool injections lose on every provider."""
    for provider in ("claude", "codex", "antigravity", "local"):
        results = {r.case: r for r in run_provider_canaries(provider)}
        for case in ("comment-injection", "filename-injection", "tool-injection"):
            assert results[case].passed, (provider, case)


def test_drift_child_hook_manual_all_providers():
    """Drift/child/hook/manual canaries pass explicitly per provider."""
    for provider in ("claude", "codex", "antigravity", "local"):
        results = {r.case: r for r in run_provider_canaries(provider)}
        for case in ("version-drift", "child-inheritance", "missing-hook",
                     "manual-equivalent", "hash-parity"):
            assert results[case].passed, (provider, case)


def test_unsupported_explicitly_recorded():
    """Unsupported capabilities are recorded, never assumed."""
    assert unsupported_recorded("codex") != []
    assert unsupported_recorded("claude") == []
    assert isinstance(unsupported_recorded("local"), list)


def test_ten_canaries_per_provider():
    """Full canary set runs live per provider (10 cases each)."""
    for provider in ("claude", "codex", "antigravity", "local"):
        assert len(run_provider_canaries(provider)) == 10, provider
