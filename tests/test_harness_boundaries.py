"""Boundary contract: provider-native shapes never enter the canonical path (#84, phase 3 of #28).

The canonical manifest and every adapter's translate() capability list must be
free of Anthropic, OpenAI and Google message/tool-call shapes and of provider-native
transport keys. Scanning is recursive over keys and string values.
"""

from pathlib import Path

import pytest

from agent_extensions.sync.harness import ADAPTERS, HarnessAdapter, canonical_manifest

REPO = Path(__file__).resolve().parents[1]

ANTHROPIC_SHAPES = {"tool_use", "tool_result", "input_schema", "anthropic_version"}
OPENAI_SHAPES = {"function_call", "tool_calls", "parameters", "role"}
GOOGLE_SHAPES = {"functionDeclarations", "function_declarations", "parts"}
NATIVE_TRANSPORT = {"url", "serverUrl", "CLAUDE_PLUGIN_ROOT"}
FORBIDDEN = ANTHROPIC_SHAPES | OPENAI_SHAPES | GOOGLE_SHAPES | NATIVE_TRANSPORT

# Distinctive names that cannot plausibly occur in a skill id or path: matched as
# substrings too, so "${CLAUDE_PLUGIN_ROOT}/x" in a value is caught.
DISTINCTIVE = (
    "tool_use", "tool_result", "input_schema", "anthropic_version",
    "function_call", "tool_calls", "functionDeclarations", "function_declarations",
    "CLAUDE_PLUGIN_ROOT", "serverUrl",
)


def _tokens(node, path="$"):
    """Yield (path, token) for every dict key and every string value, recursively."""
    if isinstance(node, dict):
        for key, value in node.items():
            yield path, key
            yield from _tokens(value, f"{path}.{key}")
    elif isinstance(node, list):
        for i, value in enumerate(node):
            yield from _tokens(value, f"{path}[{i}]")
    elif isinstance(node, str):
        yield path, node


def _forbidden_hits(node):
    hits = []
    for path, token in _tokens(node):
        if token in FORBIDDEN or any(name in token for name in DISTINCTIVE):
            hits.append((path, token))
    return hits


def _repo_manifest() -> dict:
    entries = [
        {"id": f"capability.{skill_md.parent.name}", "kind": "skill",
         "source": skill_md.parent.relative_to(REPO).as_posix()}
        for skill_md in sorted(REPO.glob("plugins/*/skills/*/SKILL.md"))
    ]
    return canonical_manifest(entries)


def _all_kinds_manifest() -> dict:
    return canonical_manifest([
        {"id": "capability.review-skill", "kind": "skill",
         "source": "plugins/general-skills/skills/skill-creator"},
        {"id": "capability.bundle", "kind": "plugin",
         "source": "plugins/community-skills"},
        {"id": "capability.review-agent", "kind": "agent",
         "source": "plugins/community-skills/agents/review-agent.md"},
        {"id": "capability.ship", "kind": "command",
         "source": "plugins/community-skills/commands/ship.md"},
        {"id": "capability.guard", "kind": "hook",
         "source": "plugins/community-skills/hooks/guard.json"},
        {"id": "capability.github-stdio", "kind": "mcp",
         "source": "plugins/community-skills/mcp/github.json", "transport": "stdio"},
        {"id": "capability.github-http", "kind": "mcp",
         "source": "plugins/community-skills/mcp/github-http.json", "transport": "http"},
        {"id": "capability.drive", "kind": "connector",
         "source": "plugins/community-skills/connectors/drive.json"},
    ])


# ------------------------------------------------ scanner self-checks

@pytest.mark.parametrize("token", sorted(FORBIDDEN))
def test_scanner_flags_each_forbidden_key(token):
    assert _forbidden_hits({"capabilities": [{"id": "capability.x", token: "v"}]})


@pytest.mark.parametrize("token", sorted(FORBIDDEN))
def test_scanner_flags_each_forbidden_value(token):
    assert _forbidden_hits({"capabilities": [{"id": "capability.x", "kind": token}]})


def test_scanner_flags_native_name_embedded_in_a_value():
    hits = _forbidden_hits({"capabilities": [{"source": "${CLAUDE_PLUGIN_ROOT}/x"}]})
    assert hits


def test_scanner_accepts_a_clean_manifest():
    assert _forbidden_hits(_all_kinds_manifest()) == []


# ------------------------------------------------ canonical path is neutral

def test_canonical_manifest_from_repo_skills_has_no_provider_shape():
    assert _forbidden_hits(_repo_manifest()) == []


def test_canonical_manifest_all_kinds_has_no_provider_shape():
    assert _forbidden_hits(_all_kinds_manifest()) == []


def test_canonical_manifest_top_level_is_version_and_capabilities_only():
    assert set(_repo_manifest()) == {"version", "capabilities"}
    assert set(_all_kinds_manifest()) == {"version", "capabilities"}


@pytest.mark.parametrize("name", sorted(ADAPTERS))
@pytest.mark.parametrize("build", [_repo_manifest, _all_kinds_manifest],
                         ids=["repo-skills", "all-kinds"])
def test_translated_capabilities_carry_no_provider_shape(name, build):
    translated = ADAPTERS[name].translate(build())
    assert _forbidden_hits(translated["capabilities"]) == []


# ------------------------------------------------ adapter contract

@pytest.mark.parametrize("name", sorted(ADAPTERS))
def test_every_adapter_satisfies_protocol(name):
    adapter = ADAPTERS[name]
    assert isinstance(adapter, HarnessAdapter)
    assert adapter.name == name


@pytest.mark.parametrize("name", sorted(ADAPTERS))
@pytest.mark.parametrize("build", [_repo_manifest, _all_kinds_manifest],
                         ids=["repo-skills", "all-kinds"])
def test_capability_report_partitions_ids(name, build):
    manifest = build()
    report = ADAPTERS[name].capability_report(manifest)
    assert set(report) == {"supported", "unsupported"}
    assert set(report["supported"]).isdisjoint(report["unsupported"])
    assert sorted(report["supported"] + report["unsupported"]) == sorted(
        cap["id"] for cap in manifest["capabilities"])


def test_gaps_non_empty_for_every_adapter_except_claude_code():
    for name, adapter in ADAPTERS.items():
        if name != "claude-code":
            assert adapter.gaps(), name


@pytest.mark.parametrize("name", sorted(ADAPTERS))
def test_every_adapter_with_gaps_records_a_reason(name):
    adapter = ADAPTERS[name]
    if adapter.gaps():
        assert adapter.gap_reason.strip(), name
        assert "\n" not in adapter.gap_reason, name
