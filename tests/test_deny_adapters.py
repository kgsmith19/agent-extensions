"""Stage 55b (EXT half #18): provider hook-layer monotonic deny."""

from agent_extensions.sync.deny_adapters import (
    HookVerdict,
    canary_matrix,
    evaluate_hook,
    self_weaken_refused,
)


def test_later_allow_after_deny_all_providers():
    """Later FORCE_ALLOW after base DENY stays DENY on every provider."""
    for provider in ("claude", "codex", "antigravity", "local"):
        result = evaluate_hook(provider, "DENY", "hook says allow")
        assert result.verdict == HookVerdict.DENY, provider


def test_hook_bug_after_deny_all_providers():
    """Hook errors after base DENY never restore authority."""
    for provider in ("claude", "codex", "antigravity", "local"):
        result = evaluate_hook(provider, "DENY", "", hook_error=True)
        assert result.verdict == HookVerdict.DENY, provider


def test_hook_may_restrict_further():
    """Base ALLOW + hook DENY restricts to DENY (restrict-only direction)."""
    result = evaluate_hook("codex", "ALLOW", "hook denied the write")
    assert result.verdict == HookVerdict.DENY


def test_ordinary_allowed_edit_low_friction():
    """Base ALLOW + clean hook allows without semantic-guard cost."""
    result = evaluate_hook("claude", "ALLOW", "hook passed clean")
    assert result.verdict == HookVerdict.ALLOW


def test_hook_error_without_deny_no_ops():
    """Hook error without a base deny NO_OPs with escalation, never silent allow."""
    result = evaluate_hook("local", "ALLOW", "", hook_error=True)
    assert result.verdict == HookVerdict.NO_OP


def test_pr_weakening_own_policy_detected():
    """Shorter replacement policy text flags weakening (self-certification tripwire)."""
    assert self_weaken_refused("strict-long-policy", "strict-long-policy")
    assert not self_weaken_refused("strict-long-policy", "loose")


def test_unknown_provider_declared_gap():
    """Unknown providers NO_OP as declared gaps."""
    result = evaluate_hook("clippy", "ALLOW", "ok")
    assert result.verdict == HookVerdict.NO_OP


def test_canary_matrix_covers_all_providers():
    """R3 pilot canary matrix spans every provider x deny case."""
    matrix = canary_matrix()
    providers = {row["provider"] for row in matrix}
    assert {"claude", "codex", "antigravity", "local", "all"} <= providers
    assert any(row["case"] == "ordinary-allowed-edit" and row["expect"] == "ALLOW"
               for row in matrix)
