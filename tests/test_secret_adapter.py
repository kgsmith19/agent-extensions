"""INT-08 (#37): Infisical-first scoped-ref adapter, AC1-AC5 + must-never-happen."""

from agent_extensions.sync.secret_adapter import (
    FakeSecretAdapter,
    Resolution,
    SecretAdapter,
    drill_blocked_with_explanation,
    parse_ref,
    scan_for_raw_secrets,
)

STORE = {"demo/prod/ci-runner/api/key": "live-value"}


def _adapter(**kw):
    adapter = SecretAdapter(store=dict(STORE), federation=dict(kw.get("federation", {})))
    adapter.mint("ci-runner", ttl_seconds=300, now=1000.0)
    return adapter


def test_valid_ref_resolves_at_use_time():
    """AC1: role/env scoped refs resolve via short-lived role tokens."""
    result = _adapter().resolve("infisical://demo/prod/ci-runner/api/key", now=1100.0)
    assert result.status == Resolution.VALUE and result.value == "live-value"


def test_fake_satisfies_same_contract():
    """AC2: the fake replacement satisfies the identical interface."""
    fake = FakeSecretAdapter()
    fake.mint("ci-runner", ttl_seconds=300, now=1000.0)
    result = fake.resolve("infisical://demo/prod/ci-runner/api/key", now=1100.0)
    assert result.status == Resolution.VALUE
    assert parse_ref("infisical://demo/prod/ci-runner/api/key")["role"] == "ci-runner"


def test_expired_token_fails_closed_no_owner_fallback():
    """AC3: expired tokens re-acquire or block — never stale values, never owner path."""
    adapter = _adapter()
    result = adapter.resolve("infisical://demo/prod/ci-runner/api/key", now=9999.0)
    assert result.status == Resolution.EXPIRED
    assert "owner" in result.reason and "never" in result.reason


def test_absent_federation_blocks_with_explanation():
    """AC5: missing federation blocks naming what is absent."""
    adapter = _adapter(federation={"ci-runner": False})
    result = adapter.resolve("infisical://demo/prod/ci-runner/api/key", now=1100.0)
    assert result.status == Resolution.BLOCKED
    assert "federation absent" in result.reason


def test_denied_federation_fails_closed():
    """Denied role assumption surfaces the denial reason."""
    adapter = _adapter(federation={"deny:ci-runner": True})
    result = adapter.resolve("infisical://demo/prod/ci-runner/api/key", now=1100.0)
    assert result.status == Resolution.DENIED


def test_no_raw_secrets_in_outputs():
    """Must-never-happen: seeded canary secret shapes are detected by scan."""
    assert scan_for_raw_secrets("leaked sk-ant-abcdefgh12345678 here") != []
    assert scan_for_raw_secrets("clean log line, nothing shaped") == []


def test_drill_blocks_with_explanation():
    """Runbook drill: blocked-with-explanation path end to end, no resolution."""
    out = drill_blocked_with_explanation(SecretAdapter(store=dict(STORE)))
    assert out["status"] == Resolution.BLOCKED.value
    assert out["reason"] and out["owner_fallback"] == "never"
