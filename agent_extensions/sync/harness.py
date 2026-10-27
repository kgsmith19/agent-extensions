"""Harness adapters over the canonical capability manifest v1 (#82, phase 1 of #28).

The canonical manifest (schema: agent_extensions/schemas/capability_manifest_v1.json)
is provider-neutral. Each HarnessAdapter renders it for one harness and declares,
explicitly, what that harness cannot do. The per-harness data (MCP transport key,
declared gaps, import instruction) lives on the adapter class and is the single
source of truth; agent_extensions/sync/adapters.py derives its constants from here.
"""

from typing import Dict, Iterable, List, Mapping, Protocol, runtime_checkable

KINDS = ("skill", "plugin", "agent", "command", "hook", "mcp", "connector")
TRANSPORTS = ("stdio", "http", "sse")
_CAP_KEYS = ("id", "kind", "source", "transport")


@runtime_checkable
class HarnessAdapter(Protocol):
    """One harness's view of the canonical manifest."""

    name: str

    def translate(self, manifest: dict) -> dict:
        """Harness-native rendering. Keeps every canonical entry; never drops one."""
        ...

    def capability_report(self, manifest: dict) -> dict:
        """{"supported": [ids], "unsupported": [ids]}; a partition of all ids."""
        ...

    def gaps(self) -> List[str]:
        """Declared surface gaps for this harness (never assumed)."""
        ...


class _BaseAdapter:
    name: str = ""
    provider: str = ""
    transport_key: str = ""
    import_instruction: str = ""
    supported_kinds: frozenset = frozenset()
    _gaps: tuple = ()

    def gaps(self) -> List[str]:
        return list(self._gaps)

    def translate(self, manifest: dict) -> dict:
        return {
            "adapter": self.name,
            "provider": self.provider,
            "version": manifest["version"],
            "transport_key": self.transport_key,
            "import_instruction": self.import_instruction,
            "capabilities": [dict(cap) for cap in manifest["capabilities"]],
        }

    def capability_report(self, manifest: dict) -> dict:
        supported, unsupported = [], []
        for cap in manifest["capabilities"]:
            target = supported if cap["kind"] in self.supported_kinds else unsupported
            target.append(cap["id"])
        return {"supported": supported, "unsupported": unsupported}


class ClaudeCodeAdapter(_BaseAdapter):
    name = "claude-code"
    provider = "claude"
    transport_key = "url"
    import_instruction = "marketplace add agent-extensions; install <plugin>"
    supported_kinds = frozenset(KINDS)
    _gaps = ()


class CodexAdapter(_BaseAdapter):
    name = "codex"
    provider = "codex"
    transport_key = "serverUrl"
    import_instruction = "link plugins/<plugin>/skills/<skill> under $CODEX_SKILLS_DIR"
    supported_kinds = frozenset({"skill", "mcp"})
    _gaps = ("hook-inheritance", "sandbox")


class AntigravityAdapter(_BaseAdapter):
    name = "antigravity"
    provider = "antigravity"
    transport_key = "serverUrl"
    import_instruction = "link plugins/<plugin> under $ANTIGRAVITY_PLUGINS_DIR"
    supported_kinds = frozenset({"skill", "plugin", "mcp"})
    _gaps = ("hook-inheritance", "sandbox")


class LocalAdapter(_BaseAdapter):
    name = "local"
    provider = "local"
    transport_key = "url"
    import_instruction = "read plugins/<plugin>/skills/<skill>/SKILL.md directly (headless)"
    supported_kinds = frozenset({"skill", "mcp"})
    _gaps = ("account-surface", "sandbox")


ADAPTERS: Dict[str, HarnessAdapter] = {
    adapter.name: adapter
    for adapter in (ClaudeCodeAdapter(), CodexAdapter(), AntigravityAdapter(), LocalAdapter())
}

# Legacy provider key (adapters.PROVIDERS) -> harness adapter name in ADAPTERS.
PROVIDER_ADAPTERS: Dict[str, str] = {
    "claude": "claude-code",
    "codex": "codex",
    "antigravity": "antigravity",
    "local": "local",
}


def canonical_manifest(entries: Iterable[Mapping], *, version: str = "1.0.0") -> dict:
    """Build a canonical v1 manifest from neutral capability entries.

    Only the whitelisted neutral keys are copied, so a provider-native key
    cannot enter the canonical form through this builder.

    Raises:
        ValueError: On unknown keys, unknown kind/transport, or duplicate ids.
    """
    capabilities: List[dict] = []
    seen: set = set()
    for entry in entries:
        extra = sorted(set(entry) - set(_CAP_KEYS))
        if extra:
            raise ValueError(f"non-canonical capability keys: {extra}")
        if entry.get("kind") not in KINDS:
            raise ValueError(f"unknown capability kind {entry.get('kind')!r}")
        if "transport" in entry and entry["transport"] not in TRANSPORTS:
            raise ValueError(f"unknown transport {entry['transport']!r}")
        if entry.get("id") in seen:
            raise ValueError(f"duplicate capability id {entry.get('id')!r}")
        seen.add(entry.get("id"))
        capabilities.append({key: entry[key] for key in _CAP_KEYS if key in entry})
    return {"version": version, "capabilities": capabilities}
