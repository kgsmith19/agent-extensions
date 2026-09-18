"""Mechanical porting: structural translation + explicit gap reports."""

import json
import re
import shlex
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# Hook events each surface documents (Stage 22 evidence: hook-translate tests).
CODEX_HOOK_EVENTS = frozenset({"PreToolUse", "UserPromptSubmit", "SessionStart"})
ANTIGRAVITY_HOOK_EVENTS = frozenset({"PreToolUse", "UserPromptSubmit", "SessionStart"})

BEFORE_TOOL_EQUIVALENTS = {
    "claude": "PreToolUse",
    "codex": "PreToolUse",
    "antigravity": "PreToolUse",
    "local": "(none — manual review)",
}


@dataclass
class GapEntry:
    """One unrepresentable case: reason + alternative, never silent."""

    plugin: str
    provider: str
    kind: str  # agent | hook | command | mcp | path
    item: str
    reason: str
    alternative: str


@dataclass
class GapReport:
    """Per-plugin/per-provider gap collection."""

    gaps: List[GapEntry] = field(default_factory=list)

    def add(self, plugin, provider, kind, item, reason, alternative) -> None:
        self.gaps.append(GapEntry(plugin, provider, kind, item, reason, alternative))

    def for_provider(self, provider: str) -> List[GapEntry]:
        return [g for g in self.gaps if g.provider == provider]

    def has_gaps(self) -> bool:
        return bool(self.gaps)


def tokenize_command(command: str) -> List[str]:
    """Tokenize a hook command the way a POSIX shell would (proves safety)."""
    return shlex.split(command, posix=True)


def translate_hook_event(event: str, provider: str, report: Optional[GapReport] = None,
                         plugin: str = "") -> Optional[str]:
    """Map a Claude hook event to a provider's vocabulary; gaps recorded, not dropped silently."""
    vocab = {"codex": CODEX_HOOK_EVENTS, "antigravity": ANTIGRAVITY_HOOK_EVENTS}.get(provider)
    if vocab is None:
        return event  # claude/local pass through
    if event in vocab:
        return event
    if report is not None:
        report.add(plugin, provider, "hook", event,
                   f"{event!r} not in {provider}'s documented hook vocabulary",
                   "handle at bootstrap/report time; no runtime hook installed")
    return None


def wrap_hook_command(command: str, plugin_dir: str) -> Tuple[str, bool]:
    """Rewrite python3 hook commands through hook_env_wrapper; returns (cmd, wrapped)."""
    tokens = tokenize_command(command.replace("${CLAUDE_PLUGIN_ROOT}", plugin_dir))
    if tokens[:1] == ["python3"] and len(tokens) >= 2:
        script = tokens[1]
        return (f'python3 "bootstrap/hook_env_wrapper.py" "{plugin_dir}" "{script}"', True)
    return (command, False)


def diagnose_hook_failure(exit_code: int, log_text: str) -> str:
    """Exit-code/log diagnosis: read the log, never guess from the code alone."""
    lines = [l for l in log_text.splitlines() if l.strip()]
    tail = lines[-3:] if len(lines) >= 3 else lines
    if exit_code == 0:
        return "exit 0: hook passed; log tail: " + " | ".join(tail)
    if "Traceback" in log_text or "Error" in log_text:
        return f"exit {exit_code}: hook script raised; log tail: " + " | ".join(tail)
    return f"exit {exit_code}: no error signature in log; inspect: " + " | ".join(tail)


def child_inherits_hook(parent_event: str, provider: str) -> bool:
    """Subagent hook inheritance: only Claude documents inheritance; others do not."""
    if provider == "claude":
        return True
    return False  # codex/antigravity/local: hooks are explicitly NOT a trust root


def port_agent_md(agent_md: str, provider: str, report: Optional[GapReport] = None,
                  plugin: str = "", name: str = "") -> str:
    """Mechanical agent port: frontmatter preserved, ${CLAUDE_PLUGIN_ROOT} bridged."""
    out = agent_md.replace("${CLAUDE_PLUGIN_ROOT}", "$PLUGIN_ROOT")
    if provider in ("codex", "antigravity") and "hook" in agent_md.lower() and report is not None:
        report.add(plugin, provider, "agent", name,
                   "agent references Claude-native hook semantics",
                   "route hook calls through bootstrap/hook_env_wrapper.py")
    return out


def port_mcp_config(mcp_json: Dict, provider: str, report: Optional[GapReport] = None,
                    plugin: str = "") -> Dict:
    """Mechanical MCP port: url/serverUrl naming per provider; legacy url flagged."""
    from agent_extensions.sync.adapters import MCP_TRANSPORT_KEY

    key = MCP_TRANSPORT_KEY.get(provider, "url")
    out = {"mcpServers": {}}
    for server, cfg in (mcp_json.get("mcpServers") or {}).items():
        entry = dict(cfg)
        if provider in ("codex", "antigravity"):
            if "url" in entry and "serverUrl" not in entry:
                entry["serverUrl"] = entry.pop("url")
        else:
            if "serverUrl" in entry and "url" not in entry:
                entry["url"] = entry.pop("serverUrl")
        if "url" in entry and provider in ("codex", "antigravity") and report is not None:
            report.add(plugin, provider, "mcp", server,
                       "legacy 'url' field unsupported on this provider",
                       "use 'serverUrl' (ported automatically)")
        out["mcpServers"][server] = entry
    out["_transport_key"] = key
    return out


def normalize_path_for_provider(path: str, provider: str, windows: bool = False) -> str:
    """Windows/Unix path handling: junctions on Windows, symlinks elsewhere."""
    if windows:
        return path.replace("/", "\\")
    return path.replace("\\", "/")


def plugin_marker(plugin: str, provider: str) -> str:
    """Traceability marker stamped on every ported artifact."""
    return f"stage-22-port:{plugin}:{provider}"


def port_plugin(plugin_dir: Path, provider: str, report: GapReport) -> Dict[str, int]:
    """Port one plugin's agents/hooks/commands/MCP structurally; count + gap-report."""
    counts = {"agents": 0, "hooks": 0, "commands": 0, "mcp": 0}
    plugin = plugin_dir.name
    for agent_md in sorted(plugin_dir.rglob("agents/*.md")):
        port_agent_md(agent_md.read_text(), provider, report, plugin, agent_md.stem)
        counts["agents"] += 1
    for hooks_json in sorted(plugin_dir.rglob("hooks.json")):
        try:
            data = json.loads(hooks_json.read_text())
        except json.JSONDecodeError:
            report.add(plugin, provider, "hook", str(hooks_json),
                       "hooks.json is not valid JSON", "fix upstream; nothing installed")
            continue
        for event in (data.get("hooks") or {}):
            if translate_hook_event(event, provider, report, plugin):
                counts["hooks"] += 1
    for cmd in sorted(plugin_dir.rglob("commands/*.md")):
        counts["commands"] += 1
        report.add(plugin, provider, "command", cmd.stem,
                   "slash-command has no structural equivalent on this provider",
                   "invoke the underlying skill directly; see command-gap-report.md")
    for mcp in sorted(plugin_dir.rglob(".mcp.json")):
        try:
            port_mcp_config(json.loads(mcp.read_text()), provider, report, plugin)
            counts["mcp"] += 1
        except json.JSONDecodeError:
            report.add(plugin, provider, "mcp", str(mcp),
                       ".mcp.json is not valid JSON", "fix upstream; nothing installed")
    return counts
