"""Render logic to apply profile selections to catalog and generate provider-specific manifests."""

from typing import Dict, List, Optional
from agent_extensions.schemas.catalog_schema import Catalog
from agent_extensions.schemas.profile_schema import Profile, LifecycleState


def render_profile_to_manifest(catalog: Catalog, profile: Profile) -> Dict[str, any]:
    """Render an active profile to a provider-specific manifest.

    Takes selected extensions from the profile and looks up their full
    definitions in the catalog, then generates a manifest for downstream
    consumption (e.g., by the provider adapter).

    Args:
        catalog: Full extension catalog
        profile: Active profile with selected extensions

    Returns:
        Dict with 'active' and 'candidate' extension entries

    Raises:
        ValueError: If a profile entry references a missing catalog entry
    """
    manifest: Dict[str, any] = {
        "active": [],
        "candidate": [],
    }

    for profile_entry in profile.entries:
        catalog_entry = catalog.get_by_id(profile_entry.semantic_id)

        if catalog_entry is None:
            raise ValueError(
                f"Profile references missing catalog entry: {profile_entry.semantic_id}"
            )

        manifest_item = {
            "semantic_id": catalog_entry.semantic_id,
            "name": catalog_entry.name,
            "provider": catalog_entry.provider,
            "version": catalog_entry.version,
            "source_pin": catalog_entry.source_pin,
            "state": profile_entry.state,
            "provider_binding": profile_entry.provider_binding,
        }

        if profile_entry.state in (LifecycleState.RESIDENT, LifecycleState.INVOKED):
            manifest["active"].append(manifest_item)
        else:
            manifest["candidate"].append(manifest_item)

    return manifest


def validate_profile_against_catalog(
    catalog: Catalog,
    profile: Profile,
) -> tuple[bool, List[str]]:
    """Validate that all profile entries exist in the catalog.

    Args:
        catalog: Full extension catalog
        profile: Active profile

    Returns:
        Tuple of (is_valid, list_of_missing_ids)
    """
    missing = []

    for profile_entry in profile.entries:
        if catalog.get_by_id(profile_entry.semantic_id) is None:
            missing.append(profile_entry.semantic_id)

    return (len(missing) == 0, missing)


def apply_provider_binding(
    manifest: Dict[str, any],
    provider_name: str,
    binding_map: Optional[Dict[str, str]] = None,
) -> Dict[str, any]:
    """Apply provider-specific bindings to a manifest.

    Maps abstract semantic IDs to concrete provider adapters/resources.

    Args:
        manifest: Rendered manifest from render_profile_to_manifest
        provider_name: Target provider (e.g., "anthropic", "openai")
        binding_map: Optional override map of semantic_id → provider_resource

    Returns:
        Provider-bound manifest
    """
    bound_manifest: Dict[str, any] = {
        "provider": provider_name,
        "active": [],
        "candidate": [],
    }

    for category in ["active", "candidate"]:
        for item in manifest[category]:
            bound_item = item.copy()

            if binding_map and item["semantic_id"] in binding_map:
                bound_item["provider_binding"] = binding_map[item["semantic_id"]]
            else:
                bound_item["provider_binding"] = f"{provider_name}-{item['version']}"

            bound_manifest[category].append(bound_item)

    return bound_manifest
