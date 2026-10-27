"""Orchestrator/implementer model tiers (issue #81, contract std#303).

Roles map to a tier (planning | fast); profiles map provider -> tier -> model
alias. Resolution order, lowest to highest precedence:

  1. built-in DEFAULT_TIERS
  2. owner file ~/.config/agents/agent-tiers.json (same shape)
  3. env AE_TIER_PLANNING / AE_TIER_FAST (override the anthropic profile)

Invalid JSON or an invalid shape raises TierConfigError; the tiers stage
reports that as failed and writes nothing. Stdlib only.
"""

from __future__ import annotations

import copy
import json
import re
from pathlib import Path
from typing import Mapping

DEFAULT_TIERS: dict = {
    "roles": {"orchestrator": "planning", "implementer": "fast", "reviewer": "planning"},
    "profiles": {"anthropic": {"planning": "opus", "fast": "haiku"}},
    "fallback": "harness-default",
}

TOP_LEVEL_KEYS = ("roles", "profiles", "fallback")
KNOWN_ROLES = ("orchestrator", "implementer", "reviewer")
REQUIRED_PROVIDER = "anthropic"  # Claude Code renders its subagents from this profile
ENV_OVERRIDES = {"AE_TIER_PLANNING": "planning", "AE_TIER_FAST": "fast"}
_ALIAS = re.compile(r"^[A-Za-z0-9._-]+$")

# Claude Code subagent definitions rendered by the tiers stage: role -> template.
CLAUDE_AGENT_TEMPLATES: dict[str, str] = {
    "implementer": "claude-agent-implementer.md",
    "reviewer": "claude-agent-reviewer.md",
}
CLAUDE_PROVIDER = REQUIRED_PROVIDER

# Declared support per harness. Only claude-code is rendered; every other
# harness is a named gap, never claimed.
TIER_SUPPORT: dict[str, str] = {
    "claude-code": "rendered: ~/.claude/agents/{implementer,reviewer}.md",
    "codex": "gap: no per-subagent model selection the installer can configure; tiers not rendered",
    "gemini": "gap: no per-subagent model selection the installer can configure; tiers not rendered",
    "kilo": "gap: no per-subagent model selection the installer can configure; tiers not rendered",
    "pi": "gap: no per-subagent model selection the installer can configure; tiers not rendered",
    "local": "gap: no harness subagent surface to render tiers into",
}


class TierConfigError(ValueError):
    """Invalid tier configuration; the message names the source and the problem."""


def tier_config_path(home: Path) -> Path:
    return Path(home) / ".config" / "agents" / "agent-tiers.json"


def gap_names() -> list[str]:
    return [name for name, state in TIER_SUPPORT.items() if state.startswith("gap:")]


def _load_file(path: Path) -> dict | None:
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise TierConfigError(f"invalid JSON in {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise TierConfigError(f"{path}: top level must be a JSON object")
    unknown = sorted(set(data) - set(TOP_LEVEL_KEYS))
    if unknown:
        raise TierConfigError(f"{path}: unknown top-level key(s): {', '.join(unknown)}")
    _check_file_shape(data, path)
    return data


def _check_file_shape(data: dict, path: Path) -> None:
    if "roles" in data:
        roles = data["roles"]
        if not isinstance(roles, dict):
            raise TierConfigError(f"{path}: roles must be an object of role -> tier")
        for role, tier in roles.items():
            if role not in KNOWN_ROLES:
                raise TierConfigError(f"{path}: unknown role {role!r}; expected one of {list(KNOWN_ROLES)}")
            if not isinstance(tier, str):
                raise TierConfigError(f"{path}: role {role!r} must map to a tier name string")
    if "profiles" in data:
        profiles = data["profiles"]
        if not isinstance(profiles, dict):
            raise TierConfigError(f"{path}: profiles must be an object of provider -> tier -> model")
        for provider, tiers in profiles.items():
            if not isinstance(tiers, dict):
                raise TierConfigError(f"{path}: profile {provider!r} must be an object of tier -> model")
            for tier, model in tiers.items():
                if not isinstance(model, str) or not _ALIAS.match(model):
                    raise TierConfigError(
                        f"{path}: profile {provider!r} tier {tier!r} must be a model alias string "
                        f"(letters, digits, '.', '_', '-'); got {model!r}"
                    )
    if "fallback" in data and not isinstance(data["fallback"], str):
        raise TierConfigError(f"{path}: fallback must be a string")


def _validate_merged(tiers: dict, source: str) -> None:
    profiles = tiers["profiles"]
    if REQUIRED_PROVIDER not in profiles:
        raise TierConfigError(f"invalid tier config ({source}): profiles must include {REQUIRED_PROVIDER!r}")
    for provider, mapping in profiles.items():
        for role, tier in tiers["roles"].items():
            if tier not in mapping:
                raise TierConfigError(
                    f"invalid tier config ({source}): profile {provider!r} has no tier {tier!r} "
                    f"required by role {role!r}"
                )
        for tier, model in mapping.items():
            if not isinstance(model, str) or not _ALIAS.match(model):
                raise TierConfigError(
                    f"invalid tier config ({source}): profile {provider!r} tier {tier!r} "
                    f"is not a model alias: {model!r}"
                )
    if not isinstance(tiers["fallback"], str):
        raise TierConfigError(f"invalid tier config ({source}): fallback must be a string")


def resolve_tiers(home: Path, env: Mapping[str, str]) -> dict:
    """Return the effective tiers: defaults < ~/.config/agents/agent-tiers.json < env.

    File keys merge into the defaults: roles and each profile merge entry by
    entry, fallback replaces. Env values (non-blank) replace the anthropic
    profile's planning / fast tiers. Raises TierConfigError on any invalid input.
    """
    path = tier_config_path(home)
    source = str(path) if path.exists() else "defaults"
    tiers = copy.deepcopy(DEFAULT_TIERS)

    file_data = _load_file(path)
    if file_data is not None:
        source = str(path)
        if "roles" in file_data:
            tiers["roles"].update(file_data["roles"])
        if "profiles" in file_data:
            for provider, mapping in file_data["profiles"].items():
                tiers["profiles"].setdefault(provider, {}).update(mapping)
        if "fallback" in file_data:
            tiers["fallback"] = file_data["fallback"]

    for var, tier in ENV_OVERRIDES.items():
        value = (env.get(var) or "").strip()
        if value:
            tiers["profiles"].setdefault(REQUIRED_PROVIDER, {})[tier] = value

    _validate_merged(tiers, source)
    return tiers


def claude_agent_models(tiers: dict) -> dict[str, str]:
    """Model alias per rendered Claude Code subagent role (implementer, reviewer)."""
    profile = tiers["profiles"][CLAUDE_PROVIDER]
    return {role: profile[tiers["roles"][role]] for role in CLAUDE_AGENT_TEMPLATES}
