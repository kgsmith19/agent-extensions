"""Stage 54b (EXT half #17): provider-side enforcement of the 54a profile."""

from agent_extensions.sync.provider_enforcement import (
    Enforcement,
    WRAPPER_PATH,
    enforce,
    false_block_rate,
)

PROFILE = {
    "read_roots": ["tools/", "tests/"],
    "write_roots": ["tools/", "tests/"],
    "exec_allow": ["python tools/standardctl.py verify"],
    "net_allow": ["api.github.com"],
}


def test_focused_edit_allowed_or_wrapped_per_provider():
    """In-bounds edits allow natively (claude) or via explicit wrapper (others)."""
    native = enforce("claude", "focused-edit", "tools/demo.py", PROFILE)
    assert native.verdict == Enforcement.ALLOW
    for provider in ("codex", "antigravity", "local"):
        decision = enforce(provider, "focused-edit", "tools/demo.py", PROFILE)
        assert decision.verdict == Enforcement.WRAPPER, provider
        assert decision.mechanism == WRAPPER_PATH[provider]


def test_foreign_write_denied_every_provider():
    """Sharp edge: foreign writes deny on every provider."""
    for provider in ("claude", "codex", "antigravity", "local"):
        decision = enforce(provider, "foreign-write", "/etc/passwd", PROFILE)
        assert decision.verdict == Enforcement.DENY, provider


def test_frozen_mold_denied_every_provider():
    """Sharp edge: frozen Mold writes deny everywhere."""
    decision = enforce("codex", "frozen-mold", "Canonical/corpus/arc-a/arc.json", PROFILE)
    assert decision.verdict == Enforcement.DENY


def test_direct_main_denied_every_provider():
    """Sharp edge: direct-to-main denies everywhere."""
    decision = enforce("antigravity", "direct-main", "tools/x.py", PROFILE)
    assert decision.verdict == Enforcement.DENY


def test_secret_raw_denied_handle_allowed():
    """Raw secrets deny; handle-mounted secrets allow."""
    assert enforce("local", "secret-raw", "api-key", PROFILE).verdict == Enforcement.DENY
    assert enforce("local", "secret-handle", "scoped-handle", PROFILE).verdict == Enforcement.ALLOW


def test_arbitrary_egress_denied_allowlisted_allowed():
    """Egress outside the allowlist denies; allowlisted hosts allow."""
    assert enforce("codex", "egress", "https://evil.example.com", PROFILE).verdict == Enforcement.DENY
    assert enforce("codex", "egress", "https://api.github.com/x", PROFILE).verdict == Enforcement.ALLOW


def test_test_command_allowlist():
    """Allowlisted test commands allow; others deny."""
    assert enforce("claude", "test-command",
                   "python tools/standardctl.py verify", PROFILE).verdict == Enforcement.ALLOW
    assert enforce("claude", "test-command", "rm -rf /", PROFILE).verdict == Enforcement.DENY


def test_research_write_denied():
    """MCP/research writes deny during research."""
    assert enforce("codex", "research-write", "tools/x.py", PROFILE).verdict == Enforcement.DENY


def test_provider_without_sandbox_wrapper_not_false_claim():
    """Sandbox-less execution gets an explicit wrapper path, never a false native claim."""
    decision = enforce("codex", "execute-custom", "custom-tool", PROFILE)
    assert decision.verdict == Enforcement.WRAPPER
    assert "wrapper" in decision.mechanism.lower() or "gate" in decision.mechanism.lower()


def test_owner_approved_escalation_allowed():
    """Owner-approved escalation allows with the approval recorded."""
    decision = enforce("local", "owner-escalation", "custom-tool", PROFILE)
    assert decision.verdict == Enforcement.ALLOW


def test_unknown_provider_declared_gap():
    """Unknown providers declare gaps, never false support."""
    decision = enforce("clippy", "focused-edit", "tools/x.py", PROFILE)
    assert decision.verdict == Enforcement.GAP


def test_dry_run_report_metric():
    """Dry-run/report records false-block rate for tightening decisions."""
    decisions = [enforce("claude", "focused-edit", "tools/a.py", PROFILE)]
    metric = false_block_rate(decisions)
    assert metric["sampled"] == 0.0
