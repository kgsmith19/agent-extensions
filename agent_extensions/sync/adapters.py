"""Provider adapters: canonical profile → provider-specific manifests, no weakening."""

import json
from typing import Dict, List, Sequence
from pydantic import BaseModel, Field

PROVIDERS = ("claude", "codex", "antigravity", "local")

# Per-provider unsupported surface, declared explicitly — never assumed.
UNSUPPORTED: Dict[str, List[str]] = {
    "claude": [],
    "codex": ["hook-inheritance", "sandbox"],
    "antigravity": ["hook-inheritance", "sandbox"],
    "local": ["account-surface", "sandbox"],
}

# MCP transport key per provider: the url/serverUrl naming difference.
MCP_TRANSPORT_KEY = {
    "claude": "url",
    "codex": "serverUrl",
    "antigravity": "serverUrl",
    "local": "url",
}

# Skill → provider import/pointer instruction (concise, per Stage 19 proof).
IMPORT_INSTRUCTIONS = {
    "claude": "marketplace add agent-extensions; install <plugin>",
    "codex": "link plugins/<plugin>/skills/<skill> under $CODEX_SKILLS_DIR",
    "antigravity": "link plugins/<plugin> under $ANTIGRAVITY_PLUGINS_DIR",
    "local": "read plugins/<plugin>/skills/<skill>/SKILL.md directly (headless)",
}


class ProviderManifest(BaseModel):
    """One provider's rendering of the canonical capability set."""

    provider: str = Field(..., description="claude | codex | antigravity | local")
    version: str = Field(default="1.0.0")
    capabilities: List[str] = Field(default_factory=list)
    unsupported: List[str] = Field(default_factory=list)
    transport_key: str = Field(...)
    import_instruction: str = Field(...)
    marker: str = Field(..., description="stage-19-manifest:<provider> sentinel")
    checksum: str = Field(..., description="sha256:<hex> over capabilities")


def normalized_capability_set(manifest: ProviderManifest) -> List[str]:
    """The semantic set every adapter must preserve exactly."""
    return sorted(manifest.capabilities)


def render_provider_manifest(
    provider: str,
    capabilities: Sequence[str],
    *,
    version: str = "1.0.0",
) -> ProviderManifest:
    """Render one provider's manifest over a canonical capability set.

    The capability list is preserved verbatim for every provider — the
    adapter layer never adds, drops, or renames entries. Only the
    transport key, unsupported declarations, import instruction, marker,
    and checksum differ per surface.

    Raises:
        ValueError: On unknown provider names.
    """
    from agent_extensions.schemas.extension_lock import compute_digest

    if provider not in PROVIDERS:
        raise ValueError(
            f"unknown provider {provider!r}; expected one of {list(PROVIDERS)}"
        )
    caps = list(capabilities)
    return ProviderManifest(
        provider=provider,
        version=version,
        capabilities=caps,
        unsupported=list(UNSUPPORTED[provider]),
        transport_key=MCP_TRANSPORT_KEY[provider],
        import_instruction=IMPORT_INSTRUCTIONS[provider],
        marker=f"stage-19-manifest:{provider}",
        checksum=compute_digest(json.dumps(sorted(caps), sort_keys=True)),
    )


def render_all_providers(
    capabilities: Sequence[str], *, version: str = "1.0.0"
) -> Dict[str, ProviderManifest]:
    """Render every provider manifest over the same canonical set."""
    return {
        provider: render_provider_manifest(provider, capabilities, version=version)
        for provider in PROVIDERS
    }
