"""Provider adapters: canonical profile → provider-specific manifests, no weakening."""

import json
from typing import Dict, List, Sequence
from pydantic import BaseModel, Field

from agent_extensions.sync.harness import ADAPTERS, PROVIDER_ADAPTERS

PROVIDERS = ("claude", "codex", "antigravity", "local")

# Per-provider data is owned by the HarnessAdapter classes in sync/harness.py;
# these module constants are derived views kept for existing callers.
UNSUPPORTED: Dict[str, List[str]] = {
    provider: ADAPTERS[PROVIDER_ADAPTERS[provider]].gaps() for provider in PROVIDERS
}

# MCP transport key per provider: the url/serverUrl naming difference.
MCP_TRANSPORT_KEY = {
    provider: ADAPTERS[PROVIDER_ADAPTERS[provider]].transport_key for provider in PROVIDERS
}

# Skill -> provider import/pointer instruction (concise, per Stage 19 proof).
IMPORT_INSTRUCTIONS = {
    provider: ADAPTERS[PROVIDER_ADAPTERS[provider]].import_instruction
    for provider in PROVIDERS
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
    adapter = ADAPTERS[PROVIDER_ADAPTERS[provider]]
    return ProviderManifest(
        provider=provider,
        version=version,
        capabilities=caps,
        unsupported=adapter.gaps(),
        transport_key=adapter.transport_key,
        import_instruction=adapter.import_instruction,
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
