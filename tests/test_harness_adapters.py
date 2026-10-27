"""Canonical capability manifest v1 and the HarnessAdapter contract (#82, phase 1 of #28).

The schema is validated by a small in-test validator for the JSON Schema subset
the v1 schema uses (stdlib only, no new dependency). Unknown keywords fail loudly
so the schema cannot silently rely on a keyword the validator does not check.
"""

import copy
import json
import re
from pathlib import Path

import pytest

from agent_extensions.sync import adapters
from agent_extensions.sync.harness import (
    ADAPTERS,
    KINDS,
    PROVIDER_ADAPTERS,
    TRANSPORTS,
    HarnessAdapter,
    canonical_manifest,
)

REPO = Path(__file__).resolve().parents[1]
SCHEMA_PATH = REPO / "agent_extensions" / "schemas" / "capability_manifest_v1.json"
FROZEN_PATH = REPO / "tests" / "fixtures" / "capability_manifest_v1.frozen.json"

# Provider-native names that must never appear in a canonical manifest.
NATIVE_NAMES = {
    "url",
    "serverUrl",
    "CLAUDE_PLUGIN_ROOT",
    "transport_key",
    "import_instruction",
    "marker",
    "checksum",
    "unsupported",
    "provider",
    "headers",
    "env",
    "allowed-tools",
}

# Exact values as they were in adapters.py before the refactor (#82).
OLD_UNSUPPORTED = {
    "claude": [],
    "codex": ["hook-inheritance", "sandbox"],
    "antigravity": ["hook-inheritance", "sandbox"],
    "local": ["account-surface", "sandbox"],
}
OLD_MCP_TRANSPORT_KEY = {
    "claude": "url",
    "codex": "serverUrl",
    "antigravity": "serverUrl",
    "local": "url",
}
OLD_IMPORT_INSTRUCTIONS = {
    "claude": "marketplace add agent-extensions; install <plugin>",
    "codex": "link plugins/<plugin>/skills/<skill> under $CODEX_SKILLS_DIR",
    "antigravity": "link plugins/<plugin> under $ANTIGRAVITY_PLUGINS_DIR",
    "local": "read plugins/<plugin>/skills/<skill>/SKILL.md directly (headless)",
}


# ---------------------------------------------------------------- validator

_ANNOTATIONS = {"$schema", "$id", "title", "description", "$comment", "$defs"}
_KEYWORDS = _ANNOTATIONS | {
    "$ref", "type", "enum", "const", "required", "properties",
    "additionalProperties", "items", "pattern", "minItems", "if", "then", "else", "not",
}
_TYPES = {
    "object": dict,
    "array": list,
    "string": str,
    "boolean": bool,
    "integer": int,
}


def _resolve(ref: str, root: dict) -> dict:
    assert ref.startswith("#/"), f"only local refs supported: {ref}"
    node = root
    for part in ref[2:].split("/"):
        node = node[part]
    return node


def _errors(instance, schema: dict, root: dict, where: str = "$") -> list:
    unknown = set(schema) - _KEYWORDS
    if unknown:
        raise NotImplementedError(f"validator does not support keywords {sorted(unknown)}")
    errs = []
    if "$ref" in schema:
        return _errors(instance, _resolve(schema["$ref"], root), root, where)
    if "type" in schema:
        expected = _TYPES[schema["type"]]
        is_bool = isinstance(instance, bool)
        ok = isinstance(instance, expected) and not (
            schema["type"] in ("integer", "string", "object", "array") and is_bool
        )
        if not ok:
            return [f"{where}: expected {schema['type']}"]
    if "enum" in schema and instance not in schema["enum"]:
        errs.append(f"{where}: {instance!r} not in enum")
    if "const" in schema and instance != schema["const"]:
        errs.append(f"{where}: must equal {schema['const']!r}")
    if isinstance(instance, str) and "pattern" in schema:
        if not re.search(schema["pattern"], instance):
            errs.append(f"{where}: {instance!r} does not match pattern")
    if isinstance(instance, list):
        if "minItems" in schema and len(instance) < schema["minItems"]:
            errs.append(f"{where}: fewer than {schema['minItems']} items")
        if "items" in schema:
            for i, item in enumerate(instance):
                errs += _errors(item, schema["items"], root, f"{where}[{i}]")
    if isinstance(instance, dict):
        for name in schema.get("required", []):
            if name not in instance:
                errs.append(f"{where}: missing required {name!r}")
        props = schema.get("properties", {})
        for name, value in instance.items():
            if name in props:
                errs += _errors(value, props[name], root, f"{where}.{name}")
            elif "additionalProperties" in schema:
                ap = schema["additionalProperties"]
                if ap is False:
                    errs.append(f"{where}: unexpected property {name!r}")
                elif isinstance(ap, dict):
                    errs += _errors(value, ap, root, f"{where}.{name}")
    if "not" in schema and not _errors(instance, schema["not"], root, where):
        errs.append(f"{where}: must not match {schema['not']!r}")
    if "if" in schema:
        branch = "then" if not _errors(instance, schema["if"], root, where) else "else"
        if branch in schema:
            errs += _errors(instance, schema[branch], root, where)
    return errs


def validate(instance, schema: dict) -> list:
    """Return a list of violations; empty means valid."""
    return _errors(instance, schema, schema)


def _schema() -> dict:
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


def _frozen() -> dict:
    return json.loads(FROZEN_PATH.read_text(encoding="utf-8"))


# ---------------------------------------------------------------- repo skills

def _repo_skill_entries() -> list:
    entries = []
    for skill_md in sorted(REPO.glob("plugins/*/skills/*/SKILL.md")):
        skill_dir = skill_md.parent
        entries.append({
            "id": f"capability.{skill_dir.name}",
            "kind": "skill",
            "source": skill_dir.relative_to(REPO).as_posix(),
        })
    return entries


def _repo_manifest() -> dict:
    return canonical_manifest(_repo_skill_entries())


def _mixed_manifest() -> dict:
    return canonical_manifest([
        {"id": "capability.skill-creator", "kind": "skill",
         "source": "plugins/general-skills/skills/skill-creator"},
        {"id": "capability.review-agent", "kind": "agent",
         "source": "plugins/community-skills/agents/review-agent.md"},
        {"id": "capability.github", "kind": "mcp",
         "source": "plugins/community-skills/mcp/github.json", "transport": "http"},
    ])


# ---------------------------------------------------------------- AC1: schema

def test_schema_file_is_draft_2020_12_with_required_top_level():
    schema = _schema()
    assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    assert schema["type"] == "object"
    assert set(schema["required"]) == {"version", "capabilities"}


def test_schema_kind_and_transport_enums_are_neutral():
    schema = _schema()
    cap = schema["$defs"]["capability"]
    assert cap["properties"]["kind"]["enum"] == list(KINDS)
    assert set(KINDS) == {"skill", "plugin", "agent", "command", "hook", "mcp", "connector"}
    assert cap["properties"]["transport"]["enum"] == list(TRANSPORTS)
    assert set(TRANSPORTS) == {"stdio", "http", "sse"}


def test_schema_rejects_provider_native_keys_on_capability():
    schema = _schema()
    cap = schema["$defs"]["capability"]
    assert cap.get("additionalProperties") is False
    for native in NATIVE_NAMES:
        assert native not in cap["properties"], native


def test_canonical_manifest_from_repo_skills_validates():
    manifest = _repo_manifest()
    assert validate(manifest, _schema()) == []
    assert manifest["version"] == "1.0.0"
    assert len(manifest["capabilities"]) == len(_repo_skill_entries()) >= 7


@pytest.mark.parametrize(
    "mutate, reason",
    [
        (lambda m: m["capabilities"][0].update(url="https://x.invalid/mcp"), "native url key"),
        (lambda m: m["capabilities"][0].update(serverUrl="https://x"), "native serverUrl key"),
        (lambda m: m["capabilities"][0].update(CLAUDE_PLUGIN_ROOT="/p"), "native env key"),
        (lambda m: m["capabilities"][0].update(kind="widget"), "unknown kind"),
        (lambda m: m["capabilities"][0].update(transport="websocket"), "unknown transport"),
        (lambda m: m["capabilities"][0].update(transport="stdio"), "transport on non-mcp kind"),
        (lambda m: m["capabilities"][0].update(id="skill-creator"), "id without capability prefix"),
        (lambda m: m["capabilities"][0].update(source="/abs/path"), "absolute source"),
        (lambda m: m["capabilities"][0].update(source="../escape"), "parent-relative source"),
        (lambda m: m.update(version="2.0.0"), "wrong major version"),
        (lambda m: m.pop("version"), "missing version"),
        (lambda m: m["capabilities"][0].pop("source"), "missing source"),
    ],
)
def test_schema_rejects_invalid_manifests(mutate, reason):
    manifest = copy.deepcopy(_repo_manifest())
    mutate(manifest)
    assert validate(manifest, _schema()), f"schema accepted: {reason}"


def test_schema_accepts_mcp_transport_neutral_value():
    manifest = _mixed_manifest()
    assert validate(manifest, _schema()) == []
    mcp = [c for c in manifest["capabilities"] if c["kind"] == "mcp"][0]
    assert mcp["transport"] == "http"


# ------------------------------------------------- AC1: additive-only freeze

def _additive_violations(frozen, live, where="$") -> list:
    """Every entry in the frozen copy must still exist unchanged in live.

    Allowed: new optional properties, new enum values, changed annotations.
    Forbidden: removed/renamed properties, removed enum values, changed
    types/patterns/consts, and any change to the required set at a node.
    """
    out = []
    if not isinstance(frozen, dict) or not isinstance(live, dict):
        if frozen != live:
            out.append(f"{where}: changed {frozen!r} -> {live!r}")
        return out
    if set(frozen.get("required", [])) != set(live.get("required", [])):
        out.append(f"{where}: required set changed")
    for key, fval in frozen.items():
        if key in ("title", "description", "$comment", "required"):
            continue
        if key not in live:
            out.append(f"{where}: removed {key!r}")
            continue
        lval = live[key]
        if key in ("properties", "$defs"):
            for name, sub in fval.items():
                if name not in lval:
                    out.append(f"{where}.{key}: removed {name!r}")
                else:
                    out += _additive_violations(sub, lval[name], f"{where}.{key}.{name}")
        elif key == "enum":
            for value in fval:
                if value not in lval:
                    out.append(f"{where}: removed enum value {value!r}")
        elif isinstance(fval, dict):
            out += _additive_violations(fval, lval, f"{where}.{key}")
        elif fval != lval:
            out.append(f"{where}: {key!r} changed {fval!r} -> {lval!r}")
    return out


def test_frozen_copy_is_additive_subset_of_live_schema():
    assert _additive_violations(_frozen(), _schema()) == []


def test_frozen_copy_matches_live_schema_today():
    """The frozen copy was taken from v1 as shipped; equal until a v1.x bump."""
    assert _frozen() == _schema()


def test_additive_checker_rejects_breaking_changes():
    """Sanity: the checker itself must fail on each forbidden change."""
    frozen = _frozen()
    cap_path = ("$defs", "capability", "properties")

    def edit(fn):
        live = copy.deepcopy(frozen)
        fn(live)
        return _additive_violations(frozen, live)

    assert edit(lambda s: s[cap_path[0]][cap_path[1]][cap_path[2]].pop("source"))
    assert edit(lambda s: s[cap_path[0]][cap_path[1]][cap_path[2]].__setitem__(
        "path", s[cap_path[0]][cap_path[1]][cap_path[2]].pop("source")))
    assert edit(lambda s: s[cap_path[0]][cap_path[1]][cap_path[2]]["kind"]["enum"].remove("mcp"))
    assert edit(lambda s: s["required"].append("owner"))
    assert edit(lambda s: s["$defs"]["capability"]["required"].append("owner"))
    assert edit(lambda s: s["properties"]["version"].update(pattern="^.*$"))


def test_additive_checker_allows_new_optional_property_and_enum_value():
    frozen = _frozen()
    live = copy.deepcopy(frozen)
    live["$defs"]["capability"]["properties"]["tags"] = {
        "type": "array", "items": {"type": "string"}}
    live["$defs"]["capability"]["properties"]["kind"]["enum"].append("bundle")
    assert _additive_violations(frozen, live) == []


# ------------------------------------------------ AC2: protocol and registry

def test_registry_has_five_adapters_implementing_protocol():
    # Oracle change (#83): "generic" fallback adapter registered in ADAPTERS.
    assert sorted(ADAPTERS) == ["antigravity", "claude-code", "codex", "generic", "local"]
    for name, adapter in ADAPTERS.items():
        assert isinstance(adapter, HarnessAdapter), name
        assert adapter.name == name


def test_provider_keys_map_to_adapters_and_keep_providers_tuple():
    assert PROVIDER_ADAPTERS == {
        "claude": "claude-code",
        "codex": "codex",
        "antigravity": "antigravity",
        "local": "local",
    }
    assert tuple(PROVIDER_ADAPTERS) == adapters.PROVIDERS == (
        "claude", "codex", "antigravity", "local")


@pytest.mark.parametrize("provider", ["claude", "codex", "antigravity", "local"])
def test_gaps_equal_old_unsupported_list(provider):
    adapter = ADAPTERS[PROVIDER_ADAPTERS[provider]]
    assert adapter.gaps() == OLD_UNSUPPORTED[provider]


@pytest.mark.parametrize("provider", ["claude", "codex", "antigravity", "local"])
def test_adapter_data_reproduces_old_module_constants(provider):
    adapter = ADAPTERS[PROVIDER_ADAPTERS[provider]]
    assert adapter.transport_key == OLD_MCP_TRANSPORT_KEY[provider]
    assert adapter.import_instruction == OLD_IMPORT_INSTRUCTIONS[provider]


def test_adapters_module_constants_derive_from_adapter_classes():
    assert adapters.UNSUPPORTED == OLD_UNSUPPORTED
    assert adapters.MCP_TRANSPORT_KEY == OLD_MCP_TRANSPORT_KEY
    assert adapters.IMPORT_INSTRUCTIONS == OLD_IMPORT_INSTRUCTIONS
    assert list(adapters.UNSUPPORTED) == list(adapters.PROVIDERS)
    assert list(adapters.MCP_TRANSPORT_KEY) == list(adapters.PROVIDERS)
    assert list(adapters.IMPORT_INSTRUCTIONS) == list(adapters.PROVIDERS)


def test_canonical_builder_rejects_provider_native_and_unknown_keys():
    with pytest.raises(ValueError):
        canonical_manifest([{"id": "capability.x", "kind": "skill",
                             "source": "plugins/a/skills/x", "url": "https://x"}])
    with pytest.raises(ValueError):
        canonical_manifest([{"id": "capability.x", "kind": "widget",
                             "source": "plugins/a/skills/x"}])


# ------------------------------------------------- AC4: canonical neutrality

def _walk(node, path="$"):
    if isinstance(node, dict):
        for key, value in node.items():
            yield path, key
            yield from _walk(value, f"{path}.{key}")
    elif isinstance(node, list):
        for i, value in enumerate(node):
            yield from _walk(value, f"{path}[{i}]")
    elif isinstance(node, str):
        yield path, node


def test_no_provider_native_field_in_canonical_repo_manifest():
    hits = []
    for path, token in _walk(_repo_manifest()):
        if token in NATIVE_NAMES or "CLAUDE_" in token:
            hits.append((path, token))
    assert hits == []


def test_no_provider_native_field_in_mixed_canonical_manifest():
    hits = [t for _, t in _walk(_mixed_manifest()) if t in NATIVE_NAMES]
    assert hits == []


# ------------------------------------------- AC4: translate preserves the set

def _ids(caps) -> list:
    return sorted(c["id"] for c in caps)


@pytest.mark.parametrize("adapter", list(ADAPTERS.values()), ids=list(ADAPTERS))
def test_translate_preserves_normalized_capability_id_set(adapter):
    manifest = _mixed_manifest()
    translated = adapter.translate(manifest)
    assert _ids(translated["capabilities"]) == _ids(manifest["capabilities"])


@pytest.mark.parametrize("adapter", list(ADAPTERS.values()), ids=list(ADAPTERS))
def test_translate_does_not_drop_or_add_entries_for_repo_skills(adapter):
    manifest = _repo_manifest()
    translated = adapter.translate(manifest)
    assert _ids(translated["capabilities"]) == _ids(manifest["capabilities"])
    assert len(translated["capabilities"]) == len(manifest["capabilities"])


def test_translate_carries_native_transport_and_import_instruction():
    translated = ADAPTERS["codex"].translate(_mixed_manifest())
    assert translated["transport_key"] == "serverUrl"
    assert translated["import_instruction"] == OLD_IMPORT_INSTRUCTIONS["codex"]


def test_translate_does_not_mutate_input():
    manifest = _mixed_manifest()
    before = copy.deepcopy(manifest)
    for adapter in ADAPTERS.values():
        adapter.translate(manifest)
    assert manifest == before


# ------------------------------------------- AC4: capability_report partition

@pytest.mark.parametrize("adapter", list(ADAPTERS.values()), ids=list(ADAPTERS))
def test_capability_report_partitions_all_ids(adapter):
    manifest = _mixed_manifest()
    report = adapter.capability_report(manifest)
    assert set(report) == {"supported", "unsupported"}
    supported, unsupported = report["supported"], report["unsupported"]
    assert set(supported).isdisjoint(unsupported)
    assert sorted(supported + unsupported) == _ids(manifest["capabilities"])


def test_capability_report_marks_non_claude_agent_gap_explicitly():
    manifest = _mixed_manifest()
    assert ADAPTERS["claude-code"].capability_report(manifest)["unsupported"] == []
    report = ADAPTERS["codex"].capability_report(manifest)
    assert report["unsupported"] == ["capability.review-agent"]
    assert "capability.skill-creator" in report["supported"]
