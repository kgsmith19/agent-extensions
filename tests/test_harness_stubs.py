"""Declared-surface stubs for kilo, pi, cline and opencode (#84, phase 3 of #28).

Each stub claims only what this repo can justify: skills. Every other canonical
kind and every MCP transport is a named gap. No environment marker maps to a
stub, so a stub is reachable through the AE_HARNESS override only, and none is
added to the legacy adapters.PROVIDERS tuple.
"""

import pytest

from agent_extensions.install.tiers import TIER_SUPPORT
from agent_extensions.sync import adapters
from agent_extensions.sync.harness import (
    ADAPTERS,
    HARNESS_MARKERS,
    KINDS,
    PROVIDER_ADAPTERS,
    HarnessAdapter,
    canonical_manifest,
    detect_harness,
)

STUBS = ("cline", "kilo", "opencode", "pi")

EXPECTED_GAPS = {
    "hooks",
    "plugins",
    "agents",
    "commands",
    "connectors",
    "mcp",
    "subagent-model-tiers",
}

KIND_TO_GAP = {
    "hook": "hooks",
    "plugin": "plugins",
    "agent": "agents",
    "command": "commands",
    "connector": "connectors",
    "mcp": "mcp",
}


def _probe() -> dict:
    """One probe entry per kind, plus stdio and http MCP, so every surface is tested."""
    entries = [
        {"id": f"capability.probe-{kind}", "kind": kind, "source": f"plugins/probe/{kind}"}
        for kind in KINDS
        if kind != "mcp"
    ]
    entries.append({"id": "capability.probe-mcp-stdio", "kind": "mcp",
                    "source": "plugins/probe/mcp-stdio.json", "transport": "stdio"})
    entries.append({"id": "capability.probe-mcp-http", "kind": "mcp",
                    "source": "plugins/probe/mcp-http.json", "transport": "http"})
    return canonical_manifest(entries)


@pytest.mark.parametrize("name", STUBS)
def test_stub_is_registered_and_implements_protocol(name):
    adapter = ADAPTERS[name]
    assert isinstance(adapter, HarnessAdapter)
    assert adapter.name == name


@pytest.mark.parametrize("name", STUBS)
def test_stub_claims_only_skills(name):
    report = ADAPTERS[name].capability_report(_probe())
    assert report["supported"] == ["capability.probe-skill"]
    assert "capability.probe-mcp-stdio" in report["unsupported"]
    assert "capability.probe-mcp-http" in report["unsupported"]


@pytest.mark.parametrize("name", STUBS)
def test_stub_transport_key_is_none(name):
    assert ADAPTERS[name].transport_key == ""


@pytest.mark.parametrize("name", STUBS)
def test_stub_gaps_are_declared_exactly(name):
    gaps = ADAPTERS[name].gaps()
    assert len(gaps) == len(set(gaps))
    assert set(gaps) == EXPECTED_GAPS


@pytest.mark.parametrize("name", STUBS)
def test_every_non_skill_kind_has_a_named_gap(name):
    gaps = set(ADAPTERS[name].gaps())
    for kind in KINDS:
        if kind != "skill":
            assert KIND_TO_GAP[kind] in gaps, kind


@pytest.mark.parametrize("name", STUBS)
def test_stub_gap_reason_is_one_declared_line(name):
    reason = ADAPTERS[name].gap_reason
    assert reason.strip()
    assert "\n" not in reason
    assert len(reason) <= 200


@pytest.mark.parametrize("name", STUBS)
def test_stub_declares_an_import_instruction(name):
    assert ADAPTERS[name].import_instruction.strip()


@pytest.mark.parametrize("name", ["kilo", "pi"])
def test_subagent_tier_gap_matches_installer_declaration(name):
    # Kilo and pi have no per-subagent model surface the installer can configure.
    assert TIER_SUPPORT[name].startswith("gap:")
    assert "subagent-model-tiers" in ADAPTERS[name].gaps()


def test_stubs_are_not_legacy_providers():
    assert not set(STUBS) & set(adapters.PROVIDERS)
    assert not set(STUBS) & set(PROVIDER_ADAPTERS.values())


def test_no_environment_marker_maps_to_a_stub():
    for name in STUBS:
        assert name not in HARNESS_MARKERS


@pytest.mark.parametrize("name", STUBS)
def test_stub_is_reachable_through_override_only(name):
    detection = detect_harness({"AE_HARNESS": name})
    assert detection.source == "override"
    assert detection.name == name
    assert detect_harness({}).name == "generic"
