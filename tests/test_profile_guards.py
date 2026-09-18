from datetime import datetime, timedelta, timezone

import pytest
from agent_extensions.schemas.profile_schema import (
    LifecycleState,
    Profile,
    ProfileEntry,
    assert_can_invoke,
    assert_transition_allowed,
    is_selectable,
    phase_of,
)


def _entry(state, semantic_id="capability.search"):
    return ProfileEntry(semantic_id=semantic_id, state=state)


def test_phase_of_maps_broad_supply_vs_task_footprint():
    """Protects footprint separation; AVAILABLE/STAGED are supply, ENABLED+ are task."""
    assert phase_of(LifecycleState.AVAILABLE) == "supply"
    assert phase_of(LifecycleState.STAGED) == "supply"
    for state in (
        LifecycleState.ENABLED,
        LifecycleState.RESIDENT,
        LifecycleState.INVOKED,
        LifecycleState.VERIFIED,
    ):
        assert phase_of(state) == "task"


def test_is_selectable_rejects_degraded():
    """Protects selection integrity; a DEGRADED item must never be selected."""
    assert not is_selectable(_entry(LifecycleState.DEGRADED))
    assert is_selectable(_entry(LifecycleState.ENABLED))
    assert is_selectable(_entry(LifecycleState.RESIDENT))


def test_assert_can_invoke_rejects_wrong_phase():
    """Protects invocation phase; only RESIDENT may be invoked."""
    with pytest.raises(ValueError, match="wrong phase|RESIDENT"):
        assert_can_invoke(_entry(LifecycleState.ENABLED))
    with pytest.raises(ValueError, match="wrong phase|RESIDENT"):
        assert_can_invoke(_entry(LifecycleState.AVAILABLE))
    # Positive control: RESIDENT invokes cleanly.
    assert_can_invoke(_entry(LifecycleState.RESIDENT))


def test_assert_transition_allowed_guards_invalid_jump():
    """Protects the state machine; invalid jumps raise with repair guidance."""
    with pytest.raises(ValueError, match="invalid.*transition"):
        assert_transition_allowed(LifecycleState.AVAILABLE, LifecycleState.INVOKED)
    # Positive control.
    assert_transition_allowed(LifecycleState.AVAILABLE, LifecycleState.STAGED)


def test_profile_rejects_duplicate_semantic_ids():
    """Protects profile integrity; one entry per semantic ID."""
    with pytest.raises(ValueError, match="duplicate"):
        Profile(entries=[_entry(LifecycleState.ENABLED), _entry(LifecycleState.RESIDENT)])


def test_profile_rejects_invoked_entry():
    """Protects phase separation; stored profiles never hold live INVOKED state."""
    with pytest.raises(ValueError, match="INVOKED"):
        Profile(entries=[_entry(LifecycleState.INVOKED)])


def test_profile_transition_to_validates_each_entry():
    """Protects batch transitions; every entry must allow from->to."""
    profile = Profile(
        entries=[
            _entry(LifecycleState.AVAILABLE, "capability.a"),
            _entry(LifecycleState.AVAILABLE, "capability.b"),
        ]
    )
    moved = profile.transition_to(LifecycleState.STAGED)
    assert all(e.state is LifecycleState.STAGED for e in moved.entries)
    with pytest.raises(ValueError, match="invalid.*transition"):
        profile.transition_to(LifecycleState.INVOKED)


def test_profile_expire_stale_residents():
    """Protects activation expiry; stale RESIDENT entries fall back to AVAILABLE."""
    now = datetime.now(timezone.utc)
    profile = Profile(
        entries=[
            ProfileEntry(
                semantic_id="capability.stale",
                state=LifecycleState.RESIDENT,
                activated_at=(now - timedelta(hours=2)).isoformat(),
                active_ttl_seconds=3600,
            ),
            ProfileEntry(
                semantic_id="capability.fresh",
                state=LifecycleState.RESIDENT,
                activated_at=now.isoformat(),
                active_ttl_seconds=3600,
            ),
        ]
    )
    expired = profile.expire_stale(now=now)
    by_id = {e.semantic_id: e.state for e in expired.entries}
    assert by_id["capability.stale"] is LifecycleState.AVAILABLE
    assert by_id["capability.fresh"] is LifecycleState.RESIDENT
