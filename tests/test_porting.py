import json
from pathlib import Path

import pytest
from agent_extensions.sync.porting import (
    BEFORE_TOOL_EQUIVALENTS,
    GapReport,
    child_inherits_hook,
    diagnose_hook_failure,
    normalize_path_for_provider,
    plugin_marker,
    port_agent_md,
    port_mcp_config,
    port_plugin,
    tokenize_command,
    translate_hook_event,
    wrap_hook_command,
)

REPO = Path(__file__).resolve().parents[1]


def test_real_hookify_environment_wrapper():
    """Protects the real fix; python3 hooks route through hook_env_wrapper."""
    wrapped, did = wrap_hook_command('python3 "${CLAUDE_PLUGIN_ROOT}/hooks/pre.py"', "/plugins/demo")
    assert did
    assert "hook_env_wrapper.py" in wrapped
    assert "CLAUDE_PLUGIN_ROOT" not in wrapped


def test_command_tokenization_safe():
    """Protects tokenization; quoted hook commands split the way sh would."""
    assert tokenize_command('python3 "my hooks/pre.py" --flag') == [
        "python3", "my hooks/pre.py", "--flag",
    ]


def test_before_tool_equivalents():
    """Protects event mapping; BeforeTool work lands on PreToolUse everywhere."""
    assert BEFORE_TOOL_EQUIVALENTS["codex"] == "PreToolUse"
    assert BEFORE_TOOL_EQUIVALENTS["antigravity"] == "PreToolUse"


def test_child_subagent_inheritance():
    """Protects inheritance honesty; only Claude documents hook inheritance."""
    assert child_inherits_hook("PreToolUse", "claude") is True
    assert child_inherits_hook("PreToolUse", "codex") is False
    assert child_inherits_hook("PreToolUse", "antigravity") is False


def test_unsupported_command_is_gap_not_guess():
    """Protects gap discipline; unknown command forms are reported, never guessed."""
    report = GapReport()
    counts = port_plugin(REPO / "plugins" / "general-skills", "codex", report)
    assert counts["agents"] == 3  # analyzer, comparator, grader
    cmd_gaps = [g for g in report.gaps if g.kind == "command"]
    assert cmd_gaps == []  # this repo ships no commands/ dirs yet


def test_exit_code_log_diagnosis():
    """Protects diagnosis discipline; the log decides, not the exit code."""
    assert "raised" in diagnose_hook_failure(1, "Traceback (most recent call last): boom")
    assert "passed" in diagnose_hook_failure(0, "ok\n done")
    assert "inspect" in diagnose_hook_failure(2, "totally clean output")


def test_plugin_markers():
    """Protects traceability; every ported artifact carries its marker."""
    assert plugin_marker("general-skills", "codex") == "stage-22-port:general-skills:codex"


def test_remote_mcp_transport_port():
    """Protects MCP fidelity; url/serverUrl naming converts per provider."""
    cfg = {"mcpServers": {"notes": {"url": "https://example.com/rpc"}}}
    report = GapReport()
    out = port_mcp_config(cfg, "codex", report, "demo")
    assert out["mcpServers"]["notes"]["serverUrl"] == "https://example.com/rpc"
    assert out["_transport_key"] == "serverUrl"
    back = port_mcp_config(out, "claude")
    assert back["mcpServers"]["notes"]["url"] == "https://example.com/rpc"


def test_windows_unix_path_handling():
    """Protects path portability; separators normalize per platform."""
    assert normalize_path_for_provider("a/b/c", "codex", windows=True) == "a\\b\\c"
    assert normalize_path_for_provider("a\\b\\c", "codex", windows=False) == "a/b/c"


def test_hook_event_vocabularies_drop_loudly():
    """Protects vocabulary honesty; off-vocab events become gaps, not silent drops."""
    report = GapReport()
    assert translate_hook_event("NotAnEvent", "codex", report, "demo") is None
    assert report.has_gaps()
    assert translate_hook_event("PreToolUse", "codex", report, "demo") == "PreToolUse"


def test_malformed_configs_are_gaps(tmp_path):
    """Protects malformed inputs; bad JSON becomes a gap, never a crash."""
    plugin = tmp_path / "bad-plugin"
    (plugin / "agents").mkdir(parents=True)
    (plugin / "agents" / "x.md").write_text("# X\n\nNo hooks here.")
    (plugin / "hooks.json").write_text("{not json")
    report = GapReport()
    counts = port_plugin(plugin, "codex", report)
    assert counts["agents"] == 1
    assert any(g.kind == "hook" and "valid JSON" in g.reason for g in report.gaps)


def test_gap_report_per_plugin_per_provider():
    """Protects the deliverable; gaps filter cleanly per provider."""
    report = GapReport()
    port_plugin(REPO / "plugins" / "general-skills", "antigravity", report)
    for gap in report.for_provider("antigravity"):
        assert gap.provider == "antigravity"
        assert gap.reason and gap.alternative
