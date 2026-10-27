"""Harness adapters over the canonical capability manifest v1 (#82, phase 1 of #28).

The canonical manifest (schema: agent_extensions/schemas/capability_manifest_v1.json)
is provider-neutral. Each HarnessAdapter renders it for one harness and declares,
explicitly, what that harness cannot do. The per-harness data (MCP transport key,
declared gaps, import instruction) lives on the adapter class and is the single
source of truth; agent_extensions/sync/adapters.py derives its constants from here.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Mapping, Optional, Protocol, Tuple, runtime_checkable

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


class GenericAdapter(_BaseAdapter):
    """Fallback for an unknown harness: the smallest surface that is safe to claim.

    Skills are read directly from SKILL.md and MCP is limited to stdio. Every
    other kind, and every non-stdio or transport-less MCP entry, is unsupported.
    """

    name = "generic"
    provider = "generic"
    transport_key = ""
    import_instruction = "read plugins/<plugin>/skills/<skill>/SKILL.md directly (stdio MCP only)"
    supported_kinds = frozenset({"skill", "mcp"})
    _gaps = ("hooks", "plugins", "agents", "commands", "connectors", "http-mcp")

    def _supports(self, cap: Mapping) -> bool:
        if cap["kind"] == "skill":
            return True
        return cap["kind"] == "mcp" and cap.get("transport") == "stdio"

    def capability_report(self, manifest: dict) -> dict:
        supported, unsupported = [], []
        for cap in manifest["capabilities"]:
            target = supported if self._supports(cap) else unsupported
            target.append(cap["id"])
        return {"supported": supported, "unsupported": unsupported}


ADAPTERS: Dict[str, HarnessAdapter] = {
    adapter.name: adapter
    for adapter in (
        ClaudeCodeAdapter(),
        CodexAdapter(),
        AntigravityAdapter(),
        LocalAdapter(),
        GenericAdapter(),
    )
}

# Environment markers that identify a harness. Each entry is
# (variable, required value or None). None means "set and non-empty".
# Only markers with a clear basis are listed; anything unsure stays out, so the
# harness is left undetected (generic fallback) rather than guessed.
HARNESS_MARKERS: Dict[str, Tuple[Tuple[str, Optional[str]], ...]] = {
    # CLAUDECODE=1 is exported by Claude Code into the shells it spawns (verified
    # present in a Claude Code shell). CLAUDE_CODE_ENTRYPOINT names the launch
    # surface and is set on the same path (verified present in a Claude Code shell).
    "claude-code": (("CLAUDECODE", "1"), ("CLAUDE_CODE_ENTRYPOINT", None)),
    # CODEX_SANDBOX is set by the Codex CLI's command sandbox (basis: the owner's
    # marker table; not verifiable in this sandbox). CODEX_HOME is deliberately
    # NOT a marker: it is a user-exported config path that can persist outside
    # any Codex run. GEMINI_CLI is NOT mapped: it identifies Gemini CLI, not the
    # antigravity harness this repo targets, so mapping it would be a guess.
    "codex": (("CODEX_SANDBOX", None),),
}

# Reserved for future filesystem-based detection. Detection is environment-only
# in this version, so `home` is accepted for signature stability and not read.


@dataclass(frozen=True)
class HarnessDetection:
    """Which harness was chosen, and why.

    source: "override" (AE_HARNESS), "env" (a marker), or "fallback" (generic).
    """

    name: str
    source: str
    evidence: str


def detect_harness(env: Mapping[str, str], home: Optional[Path] = None) -> HarnessDetection:
    """Resolve the harness: AE_HARNESS override, else markers, else generic fallback.

    Raises:
        ValueError: When AE_HARNESS is set to a name that is not a registered
            adapter. An unknown override is reported, never silently ignored.
    """
    override = (env.get("AE_HARNESS") or "").strip()
    if override:
        if override not in ADAPTERS:
            raise ValueError(
                f"unknown AE_HARNESS {override!r}; expected one of {sorted(ADAPTERS)}"
            )
        return HarnessDetection(name=override, source="override", evidence=f"AE_HARNESS={override}")

    matched: List[Tuple[str, str]] = []
    for harness, markers in HARNESS_MARKERS.items():
        for var, want in markers:
            value = env.get(var) or ""
            if value and (want is None or value == want):
                matched.append((harness, var))
                break

    if len(matched) == 1:
        harness, var = matched[0]
        return HarnessDetection(name=harness, source="env", evidence=f"{var} set")
    if len(matched) > 1:
        names = ", ".join(f"{h} via {v}" for h, v in matched)
        return HarnessDetection(
            name="generic", source="fallback", evidence=f"ambiguous markers: {names}"
        )
    return HarnessDetection(
        name="generic", source="fallback", evidence="no harness marker in environment"
    )

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
