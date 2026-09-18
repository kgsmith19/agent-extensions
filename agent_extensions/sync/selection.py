"""Task selection: build tiny task-specific profiles from broad catalog supply."""

from typing import List, Optional
from agent_extensions.schemas.catalog_schema import Catalog
from agent_extensions.schemas.profile_schema import (
    LifecycleState,
    Profile,
    ProfileEntry,
    assert_can_invoke,
    is_selectable,
)


def build_profile(
    catalog: Catalog,
    selected_ids: List[str],
    initial_state: LifecycleState = LifecycleState.ENABLED,
    provider_bindings: Optional[dict] = None,
) -> Profile:
    """Build a profile from an explicit selected-ID set.

    Every ID must exist in the catalog, must not be disabled, and the
    initial state must itself be selectable (DEGRADED/INVOKED rejected).

    Raises:
        ValueError: On missing, disabled, or unselectable selections.
    """
    probe = ProfileEntry(semantic_id="capability.probe", state=initial_state)
    if not is_selectable(probe):
        raise ValueError(
            f"initial_state {initial_state.value} is not selectable; "
            "profiles cannot start DEGRADED"
        )
    if initial_state is LifecycleState.INVOKED:
        # Double-guard: the schema validator message is the contract here.
        raise ValueError("INVOKED is live execution state and cannot be stored in a profile")

    entries = []
    for semantic_id in selected_ids:
        catalog_entry = catalog.get_by_id(semantic_id)
        if catalog_entry is None:
            raise ValueError(
                f"unknown capability {semantic_id}; "
                "selected IDs must reference catalog entries verbatim"
            )
        if catalog_entry.disabled:
            raise ValueError(
                f"capability {semantic_id} is disabled in this deployment and cannot be selected"
            )
        binding = (provider_bindings or {}).get(semantic_id)
        entries.append(
            ProfileEntry(
                semantic_id=semantic_id,
                state=initial_state,
                provider_binding=binding,
            )
        )
    return Profile(entries=entries)


def assert_profile_subset_of_catalog(
    catalog: Catalog, profile: Profile, selected_ids: List[str]
) -> None:
    """Fail when the profile loads any body outside the requested selected set."""
    selected = set(selected_ids)
    unselected = [e.semantic_id for e in profile.entries if e.semantic_id not in selected]
    if unselected:
        raise ValueError(
            f"profile loads unselected body not in the selected set: {sorted(unselected)}; "
            f"selected set was {sorted(selected)}"
        )
    # Every profile entry must also exist in the catalog.
    for entry in profile.entries:
        if catalog.get_by_id(entry.semantic_id) is None:
            raise ValueError(
                f"Profile references missing catalog entry: {entry.semantic_id}"
            )


def select_for_task(catalog: Catalog, selected_ids: List[str]) -> Profile:
    """Select a tiny task footprint (ENABLED) and verify it against the catalog."""
    profile = build_profile(catalog, selected_ids, initial_state=LifecycleState.ENABLED)
    assert_profile_subset_of_catalog(catalog, profile, selected_ids)
    return profile


def assert_invocable_in_phase(entry: ProfileEntry) -> None:
    """Alias guard: only RESIDENT entries may be invoked (wrong-phase rejected)."""
    assert_can_invoke(entry)
