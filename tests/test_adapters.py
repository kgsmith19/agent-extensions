from agent_extensions.sync.adapters import (
    IMPORT_INSTRUCTIONS,
    MCP_TRANSPORT_KEY,
    PROVIDERS,
    UNSUPPORTED,
    ProviderManifest,
    normalized_capability_set,
    render_all_providers,
    render_provider_manifest as render_adapter_manifest,
)
from agent_extensions.sync.selection import select_for_task
from agent_extensions.sync.migrate import migrate_marketplace
from agent_extensions.schemas.extension_lock import compute_digest
import json


def _catalog():
    return migrate_marketplace(
        [
            {
                "name": "skill-creator",
                "source": "plugins/general-skills/skills/skill-creator",
                "version": "1.0.0",
                "source_pin": "0a64e398ec6bb34a494f0c347e8ccae53a862f8e",
                "license": "Apache-2.0",
                "provider": "anthropic",
            }
        ]
    )


def render_provider_manifest(provider: str, capabilities=None) -> ProviderManifest:
    """Shared test helper: build one provider's manifest over a capability set."""
    caps = capabilities if capabilities is not None else ["capability.skill-creator"]
    return render_adapter_manifest(provider, caps)


def test_equivalent_normalized_set_across_adapters():
    """Protects no-weakening; every adapter preserves the identical semantic set."""
    sets = [normalized_capability_set(render_provider_manifest(p)) for p in PROVIDERS]
    assert all(s == sets[0] for s in sets)
    assert sets[0] == ["capability.skill-creator"]


def test_unsupported_hook_inheritance_declared():
    """Protects honesty; hook inheritance gaps are declared, not assumed."""
    assert "hook-inheritance" in render_provider_manifest("codex").unsupported
    assert "hook-inheritance" in render_provider_manifest("antigravity").unsupported
    assert "hook-inheritance" not in render_provider_manifest("claude").unsupported


def test_command_gap_is_explicit_empty_set():
    """Protects gap reporting; a provider with no commands renders empty, not absent."""
    manifest = render_provider_manifest("local", capabilities=[])
    assert manifest.capabilities == []
    assert manifest.marker == "stage-19-manifest:local"


def test_mcp_url_naming_difference():
    """Protects transport fidelity; url vs serverUrl is per-provider, never guessed."""
    assert render_provider_manifest("claude").transport_key == "url"
    assert render_provider_manifest("codex").transport_key == "serverUrl"
    assert render_provider_manifest("antigravity").transport_key == "serverUrl"


def test_manifest_marker_and_checksum():
    """Protects traceability; every manifest carries its sentinel + digest."""
    for provider in PROVIDERS:
        manifest = render_provider_manifest(provider)
        assert manifest.marker == f"stage-19-manifest:{provider}"
        assert manifest.checksum.startswith("sha256:")
        assert len(manifest.checksum) == len("sha256:") + 64


def test_env_wrapper_documented_for_hooks():
    """Protects hook bridging; non-Claude providers route hooks via the wrapper."""
    for provider in ("codex", "antigravity"):
        assert "hook-inheritance" in UNSUPPORTED[provider]
    # Claude-native hooks need no wrapper; the wrapper exists for the others.
    assert "hook-inheritance" not in UNSUPPORTED["claude"]


def test_sandbox_unavailability_declared():
    """Protects sandbox honesty; providers without sandbox say so."""
    for provider in ("codex", "antigravity", "local"):
        assert "sandbox" in render_provider_manifest(provider).unsupported


def test_provider_version_change_invalidates_checksum():
    """Protects version sensitivity; capability change yields a new digest."""
    before = render_provider_manifest("claude")
    after = render_provider_manifest(
        "claude", capabilities=["capability.skill-creator", "capability.canvas-design"]
    )
    assert before.checksum != after.checksum


def test_manual_account_surface_local_only():
    """Protects account-surface scoping; only local declares the manual surface."""
    assert "account-surface" in render_provider_manifest("local").unsupported
    assert "account-surface" not in render_provider_manifest("claude").unsupported


def test_canonical_profile_renders_all_four():
    """Protects end-to-end; one canonical profile feeds all four adapters."""
    catalog = _catalog()
    profile = select_for_task(catalog, ["capability.skill-creator"])
    ids = [e.semantic_id for e in profile.entries]
    manifests = [render_provider_manifest(p, capabilities=ids) for p in PROVIDERS]
    assert all(m.capabilities == ["capability.skill-creator"] for m in manifests)


def test_render_all_providers_rejects_unknown_provider():
    """Protects adapter discipline; unknown providers fail loudly, never default."""
    import pytest

    with pytest.raises(ValueError, match="unknown provider"):
        render_adapter_manifest("clippy", ["capability.skill-creator"])


def test_render_all_providers_helper_covers_every_surface():
    """Protects coverage; the helper renders all four providers over one set."""
    manifests = render_all_providers(["capability.skill-creator"])
    assert sorted(manifests) == sorted(PROVIDERS)
    sets = [normalized_capability_set(m) for m in manifests.values()]
    assert all(s == ["capability.skill-creator"] for s in sets)
