"""Harness detection and the generic fallback adapter (#83, phase 2 of #28).

Detection: AE_HARNESS override wins; otherwise well-known environment markers
name a harness; otherwise the generic fallback is used. An unknown override is
an error, never a silent fallback. The generic adapter declares a deliberately
small capability set and must never claim anything outside it.
"""

import itertools
from pathlib import Path

import pytest

from agent_extensions.sync.bootstrap import APPLIED, FAILED, bootstrap
from agent_extensions.sync.harness import (
    ADAPTERS,
    HARNESS_MARKERS,
    KINDS,
    TRANSPORTS,
    GenericAdapter,
    HarnessAdapter,
    HarnessDetection,
    canonical_manifest,
    detect_harness,
)

REPO = Path(__file__).resolve().parents[1]

# Hard-coded on purpose: tests must not drift with the marker table they check.
MARKER_VARS = ("AE_HARNESS", "CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT", "CODEX_SANDBOX")

GENERIC_GAPS = ("hooks", "plugins", "agents", "commands", "connectors", "http-mcp")


def _clean_env(monkeypatch):
    for var in MARKER_VARS:
        monkeypatch.delenv(var, raising=False)


# ------------------------------------------------ AC1: override and markers

@pytest.mark.parametrize("name", sorted(ADAPTERS))
def test_override_names_each_registered_harness(name):
    detection = detect_harness({"AE_HARNESS": name})
    assert detection == HarnessDetection(name=name, source="override",
                                         evidence=f"AE_HARNESS={name}")


@pytest.mark.parametrize(
    "env",
    [
        {"AE_HARNESS": "codex", "CLAUDECODE": "1", "CLAUDE_CODE_ENTRYPOINT": "cli"},
        {"AE_HARNESS": "local", "CODEX_SANDBOX": "seatbelt"},
        {"AE_HARNESS": "generic", "CLAUDECODE": "1"},
    ],
)
def test_override_wins_over_env_markers(env):
    detection = detect_harness(env)
    assert detection.source == "override"
    assert detection.name == env["AE_HARNESS"]


@pytest.mark.parametrize(
    "env, expected",
    [
        ({"CLAUDECODE": "1"}, "claude-code"),
        ({"CLAUDE_CODE_ENTRYPOINT": "cli"}, "claude-code"),
        ({"CODEX_SANDBOX": "seatbelt"}, "codex"),
    ],
)
def test_each_marker_maps_to_its_harness(env, expected):
    detection = detect_harness(env)
    assert detection.name == expected
    assert detection.source == "env"
    assert detection.evidence.startswith(next(iter(env)))


@pytest.mark.parametrize(
    "env",
    [
        {"CLAUDECODE": "0"},  # exact value required for CLAUDECODE
        {"CLAUDECODE": ""},
        {"CODEX_HOME": "/home/u/.codex"},  # user config path, deliberately not a marker
        {"GEMINI_CLI": "1"},  # identifies Gemini CLI, not the antigravity harness
    ],
)
def test_non_marker_values_do_not_detect_a_harness(env):
    detection = detect_harness(env)
    assert detection.name == "generic"
    assert detection.source == "fallback"


@pytest.mark.parametrize("env", [{}, {"PATH": "/usr/bin:/bin", "HOME": "/root"}])
def test_no_markers_falls_back_to_generic(env):
    detection = detect_harness(env)
    assert detection.name == "generic"
    assert detection.source == "fallback"
    assert detection.evidence


def test_ambiguous_markers_fall_back_to_generic_not_a_guess():
    detection = detect_harness({"CLAUDECODE": "1", "CODEX_SANDBOX": "seatbelt"})
    assert detection.name == "generic"
    assert detection.source == "fallback"
    assert "ambiguous" in detection.evidence


def test_empty_override_is_treated_as_unset():
    detection = detect_harness({"AE_HARNESS": "", "CLAUDECODE": "1"})
    assert detection.name == "claude-code"
    assert detection.source == "env"


def test_unknown_override_raises_and_lists_valid_names():
    with pytest.raises(ValueError) as info:
        detect_harness({"AE_HARNESS": "cursor"})
    message = str(info.value)
    assert "cursor" in message
    for name in sorted(ADAPTERS):
        assert name in message


def test_unknown_override_is_not_masked_by_env_markers():
    with pytest.raises(ValueError):
        detect_harness({"AE_HARNESS": "bogus", "CLAUDECODE": "1"})


def test_marker_table_names_only_registered_harnesses():
    assert set(HARNESS_MARKERS) <= set(ADAPTERS)
    assert "generic" not in HARNESS_MARKERS
    assert "antigravity" not in HARNESS_MARKERS


# ---------------------------------------------- AC2: GenericAdapter contract

def test_generic_is_registered_and_implements_protocol():
    assert isinstance(ADAPTERS["generic"], GenericAdapter)
    assert isinstance(ADAPTERS["generic"], HarnessAdapter)
    assert ADAPTERS["generic"].name == "generic"


def test_generic_gaps_are_declared_exactly():
    assert ADAPTERS["generic"].gaps() == list(GENERIC_GAPS)


def _entry(kind, transport, idx=0):
    entry = {"id": f"capability.e{idx}", "kind": kind, "source": "plugins/a/skills/x"}
    if transport is not None:
        entry["transport"] = transport
    return entry


_TRANSPORT_CASES = (None,) + tuple(TRANSPORTS)  # None = transport absent


@pytest.mark.parametrize("kind, transport", list(itertools.product(KINDS, _TRANSPORT_CASES)))
def test_generic_supports_only_declared_kind_and_transport(kind, transport):
    """Property over every kind x transport (and absent transport)."""
    manifest = canonical_manifest([_entry(kind, transport)])
    report = GenericAdapter().capability_report(manifest)
    declared = kind == "skill" or (kind == "mcp" and transport == "stdio")
    assert report["supported"] == (["capability.e0"] if declared else [])
    assert report["unsupported"] == ([] if declared else ["capability.e0"])


def test_generic_never_claims_outside_declared_set_on_full_manifest():
    entries = [
        _entry(kind, transport, idx)
        for idx, (kind, transport) in enumerate(
            itertools.product(KINDS, _TRANSPORT_CASES))
    ]
    manifest = canonical_manifest(entries)
    report = GenericAdapter().capability_report(manifest)
    by_id = {cap["id"]: cap for cap in manifest["capabilities"]}
    assert set(report["supported"]).isdisjoint(report["unsupported"])
    assert sorted(report["supported"] + report["unsupported"]) == sorted(by_id)
    for cap_id in report["supported"]:
        cap = by_id[cap_id]
        assert cap["kind"] in ("skill", "mcp")
        assert cap["kind"] == "skill" or cap.get("transport") == "stdio"
    for kind in ("hook", "plugin", "agent", "command", "connector"):
        assert all(by_id[i]["kind"] != kind for i in report["supported"])


def test_generic_rejects_http_and_sse_mcp():
    manifest = canonical_manifest([
        _entry("mcp", "http", 0),
        _entry("mcp", "sse", 1),
        _entry("mcp", "stdio", 2),
    ])
    report = GenericAdapter().capability_report(manifest)
    assert report["supported"] == ["capability.e2"]
    assert sorted(report["unsupported"]) == ["capability.e0", "capability.e1"]


def test_generic_translate_keeps_every_entry():
    manifest = canonical_manifest([_entry(kind, None, i) for i, kind in enumerate(KINDS)])
    translated = GenericAdapter().translate(manifest)
    assert [c["id"] for c in translated["capabilities"]] == [
        c["id"] for c in manifest["capabilities"]
    ]


# ------------------------------------------------- AC3: bootstrap harness stage

def _stage_names(report):
    return [s.name for s in report.stages]


def test_harness_stage_sits_right_after_detect(tmp_path):
    report = bootstrap(REPO, home=tmp_path / "home", env={})
    names = _stage_names(report)
    assert names[:3] == ["detect", "harness", "locks"]
    assert names == [
        "detect", "harness", "locks", "catalog", "profile", "render", "link", "read-back",
    ]


def test_known_harness_is_applied_with_source_detail(tmp_path):
    report = bootstrap(REPO, home=tmp_path / "home", env={"CLAUDECODE": "1"})
    stage = report.by_name("harness")
    assert stage.status == APPLIED
    assert stage.detail == "harness=claude-code (source=env)"
    assert report.ok()


def test_override_is_applied_with_override_source(tmp_path):
    report = bootstrap(REPO, home=tmp_path / "home", env={"AE_HARNESS": "codex"})
    assert report.by_name("harness").detail == "harness=codex (source=override)"


def test_generic_fallback_is_degraded_not_failed(tmp_path):
    report = bootstrap(REPO, home=tmp_path / "home", env={})
    stage = report.by_name("harness")
    assert stage.status == APPLIED
    assert stage.status != FAILED
    assert stage.detail.startswith("degraded: unknown harness, generic fallback")
    for gap in GENERIC_GAPS:
        assert gap in stage.detail
    assert report.ok()


def test_explicit_generic_override_is_also_degraded(tmp_path):
    report = bootstrap(REPO, home=tmp_path / "home", env={"AE_HARNESS": "generic"})
    stage = report.by_name("harness")
    assert stage.status == APPLIED
    assert stage.detail.startswith("degraded: unknown harness, generic fallback")


def test_invalid_override_fails_only_harness_stage(tmp_path):
    report = bootstrap(REPO, home=tmp_path / "home", env={"AE_HARNESS": "bogus"})
    stage = report.by_name("harness")
    assert stage.status == FAILED
    assert "ValueError" in stage.detail
    assert "bogus" in stage.detail
    for name in ("detect", "locks", "catalog", "profile", "render", "link", "read-back"):
        assert report.by_name(name).status == APPLIED, name


def test_bootstrap_env_defaults_to_process_environment(tmp_path, monkeypatch):
    _clean_env(monkeypatch)
    monkeypatch.setenv("CLAUDECODE", "1")
    report = bootstrap(REPO, home=tmp_path / "home")
    assert report.by_name("harness").detail == "harness=claude-code (source=env)"


def test_bootstrap_env_default_honours_invalid_override(tmp_path, monkeypatch):
    _clean_env(monkeypatch)
    monkeypatch.setenv("AE_HARNESS", "bogus")
    report = bootstrap(REPO, home=tmp_path / "home")
    assert report.by_name("harness").status == FAILED
