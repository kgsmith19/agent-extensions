from datetime import datetime, timedelta, timezone

import pytest
from agent_extensions.sync.mcp_governance import (
    ConnectorKind,
    McpServer,
    advisory_warnings,
    check_egress,
    record_activation,
    scan_secrets,
    validate_activation,
)


def _server(**kw):
    base = dict(name="notes-mcp", transport="stdio", endpoint="stdio:notes-server")
    base.update(kw)
    return McpServer(**base)


def test_too_many_active_is_advisory_not_violation():
    """Protects pilot policy; 5 active servers warn, they do not fail closed."""
    servers = [_server(name=f"s{i}") for i in range(5)]
    assert validate_activation(servers[0], active_count=5) == []
    warnings = advisory_warnings(servers, [])
    assert any("advisory" in w for w in warnings)


def test_giant_schema_is_advisory():
    """Protects schema budgets; giant schemas warn instead of refusing."""
    server = _server(schema_bytes=10 * 1024 * 1024)
    assert validate_activation(server) == []
    assert any("giant schema" in w for w in advisory_warnings([server], []))


def test_unused_capability_is_advisory():
    """Protects footprint hygiene; unused candidates warn, they still validate."""
    server = _server(capabilities=[])
    assert validate_activation(server) == []
    assert any("unused" in w for w in advisory_warnings([], [server]))


def test_duplicate_native_mcp_capability_fails():
    """Protects precedence; native tools win, MCP duplicates fail closed."""
    server = _server(capabilities=["capability.search"])
    violations = validate_activation(server, native_capabilities=["capability.search"])
    assert any("native tool wins" in v for v in violations)


def test_secret_exposure_fails_closed():
    """Protects the security boundary; secrets in endpoints never activate."""
    server = _server(endpoint="https://example.com/?key=sk-ant-abcdefgh1234")
    violations = validate_activation(server)
    assert any("secret exposure" in v for v in violations)
    assert scan_secrets("token ghp_abcdefgh1234567890") != []


def test_write_connector_during_research_fails():
    """Protects phase discipline; research never activates writers."""
    server = _server(kind=ConnectorKind.WRITE)
    violations = validate_activation(server, phase="research")
    assert any("research phase" in v for v in violations)
    assert validate_activation(server, phase="task") == []


def test_non_idempotent_write_fails():
    """Protects write safety; writers must declare idempotency."""
    server = _server(kind=ConnectorKind.WRITE, idempotent=False)
    assert any("idempotency" in v for v in validate_activation(server))


def test_egress_violation_fails():
    """Protects egress; off-allowlist hosts are refused."""
    server = _server(transport="http", endpoint="https://evil.example.com/rpc")
    assert any("egress violation" in v for v in validate_activation(server))
    assert check_egress("https://github.com/org/repo") is None
    assert check_egress("stdio:local-server") is None


def test_stale_session_fails():
    """Protects session freshness; expired activations are stale, not valid."""
    past = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
    server = _server(activated_at=past, ttl_seconds=3600)
    assert any("stale session" in v for v in validate_activation(server))


def test_server_startup_failure_shape():
    """Protects startup honesty; bad transport declarations fail at construction."""
    with pytest.raises(ValueError, match="stdio\\|http"):
        McpServer(name="bad", transport="websocket")


def test_provider_transport_mismatch_surfaced():
    """Protects transport fidelity; per-provider keys resolve without guessing."""
    from agent_extensions.sync.adapters import MCP_TRANSPORT_KEY

    assert MCP_TRANSPORT_KEY["claude"] == "url"
    assert MCP_TRANSPORT_KEY["codex"] == "serverUrl"
    assert validate_activation(_server(transport="http"), provider="codex") == []


def test_activation_updates_capsule_and_budget():
    """Protects live state; activation stamps the Task Capsule + Context Budget."""
    capsule, budget = {}, {}
    stamped = record_activation(_server(), capsule, budget)
    assert stamped.activated_at is not None
    assert capsule["active_servers"] == ["notes-mcp"]
    assert budget["mcp_active"] == 1
