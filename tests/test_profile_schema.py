import pytest
from agent_extensions.schemas.profile_schema import (
    Profile,
    ProfileEntry,
    LifecycleState,
    validate_state_transition,
)


def test_profile_rejects_invalid_lifecycle_transition():
    """Protects state machine; catches invalid phase transitions."""
    # AVAILABLE → INVOKED is invalid (must go AVAILABLE → STAGED → ENABLED → RESIDENT → INVOKED)
    result = validate_state_transition(LifecycleState.AVAILABLE, LifecycleState.INVOKED)
    assert not result, "Should reject AVAILABLE → INVOKED transition"

    # AVAILABLE → STAGED is valid
    result = validate_state_transition(LifecycleState.AVAILABLE, LifecycleState.STAGED)
    assert result, "Should accept AVAILABLE → STAGED transition"


def test_full_lifecycle_chain_is_valid():
    """Protects state machine; the advertised forward chain must be walkable."""
    chain = [
        LifecycleState.AVAILABLE,
        LifecycleState.STAGED,
        LifecycleState.ENABLED,
        LifecycleState.RESIDENT,
        LifecycleState.INVOKED,
        LifecycleState.VERIFIED,
    ]
    for from_state, to_state in zip(chain, chain[1:]):
        assert validate_state_transition(from_state, to_state), (
            f"{from_state} → {to_state} should be valid"
        )


def test_invoked_can_degrade():
    """Protects failure handling; INVOKED → DEGRADED is a first-class transition."""
    assert validate_state_transition(LifecycleState.INVOKED, LifecycleState.DEGRADED)


def test_degraded_recovers_to_available():
    """Protects failure recovery; DEGRADED → AVAILABLE is the only exit."""
    assert validate_state_transition(LifecycleState.DEGRADED, LifecycleState.AVAILABLE)
    assert not validate_state_transition(LifecycleState.DEGRADED, LifecycleState.ENABLED)


def test_profile_counts_extensions_by_state():
    """Positive control: state counting works."""
    profile = Profile(
        entries=[
            ProfileEntry(semantic_id="capability.search", state=LifecycleState.ENABLED),
            ProfileEntry(semantic_id="capability.math", state=LifecycleState.ENABLED),
            ProfileEntry(semantic_id="capability.storage", state=LifecycleState.RESIDENT),
        ]
    )

    assert profile.count_by_state(LifecycleState.ENABLED) == 2
    assert profile.count_by_state(LifecycleState.RESIDENT) == 1
    assert profile.count_by_state(LifecycleState.AVAILABLE) == 0


def test_profile_entry_requires_valid_semantic_id():
    """Protects semantic ID format."""
    with pytest.raises(ValueError, match="must start with"):
        ProfileEntry(semantic_id="search", state=LifecycleState.ENABLED)


def test_profile_state_parses_from_string():
    """Protects JSON round-trip; state parses from raw string values."""
    entry = ProfileEntry(semantic_id="capability.search", state="ENABLED")
    assert entry.state is LifecycleState.ENABLED
