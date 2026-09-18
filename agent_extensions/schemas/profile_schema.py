"""Profile schema with lifecycle state machine for agent extensions.

Defines lifecycle states, valid transitions, and the Profile aggregate:
AVAILABLE → STAGED → ENABLED → RESIDENT → INVOKED → {VERIFIED, DEGRADED}
"""

from datetime import datetime, timezone
from enum import Enum
from typing import List, Optional
from pydantic import BaseModel, Field, field_validator


class LifecycleState(str, Enum):
    """Extension lifecycle states."""

    AVAILABLE = "AVAILABLE"  # In catalog but not loaded
    STAGED = "STAGED"        # Loaded metadata only
    ENABLED = "ENABLED"      # Ready for task selection
    RESIDENT = "RESIDENT"    # Kept in memory for recurring use
    INVOKED = "INVOKED"      # Currently executing
    VERIFIED = "VERIFIED"    # Passed conformance check
    DEGRADED = "DEGRADED"    # Failed or fallback state


VALID_TRANSITIONS = {
    LifecycleState.AVAILABLE: {LifecycleState.STAGED},
    LifecycleState.STAGED: {LifecycleState.ENABLED, LifecycleState.AVAILABLE},
    LifecycleState.ENABLED: {LifecycleState.RESIDENT, LifecycleState.AVAILABLE},
    LifecycleState.RESIDENT: {LifecycleState.INVOKED, LifecycleState.AVAILABLE},
    LifecycleState.INVOKED: {
        LifecycleState.VERIFIED,
        LifecycleState.DEGRADED,
        LifecycleState.AVAILABLE,
    },
    LifecycleState.VERIFIED: {LifecycleState.RESIDENT, LifecycleState.AVAILABLE},
    LifecycleState.DEGRADED: {LifecycleState.AVAILABLE},
}


def validate_state_transition(from_state: LifecycleState, to_state: LifecycleState) -> bool:
    """Check if a state transition is allowed."""
    return to_state in VALID_TRANSITIONS.get(from_state, set())


class ProfileEntry(BaseModel):
    """One active or resident extension in the profile."""

    semantic_id: str = Field(..., description="Semantic capability ID")
    state: LifecycleState = Field(
        default=LifecycleState.AVAILABLE, description="Current lifecycle state"
    )
    provider_binding: Optional[str] = Field(
        None, description="Provider-specific binding (e.g., 'anthropic-v1')"
    )
    activated_at: Optional[str] = Field(
        None, description="ISO-8601 timestamp when the entry became task-active"
    )
    active_ttl_seconds: Optional[int] = Field(
        None, description="Seconds after activated_at when a RESIDENT entry expires"
    )

    @field_validator("semantic_id")
    @classmethod
    def validate_semantic_id(cls, v):
        if not v.startswith("capability."):
            raise ValueError("semantic_id must start with 'capability.'")
        return v

    @field_validator("state")
    @classmethod
    def validate_stored_state(cls, v):
        if v is LifecycleState.INVOKED:
            raise ValueError("INVOKED is live execution state and cannot be stored in a profile")
        return v


class Profile(BaseModel):
    """Active/resident profile — selected extensions for current task."""

    entries: List[ProfileEntry] = Field(
        default_factory=list, description="Selected extensions"
    )
    version: str = Field(default="1.0.0", description="Profile schema version")

    @field_validator("entries")
    @classmethod
    def validate_unique_ids(cls, entries):
        ids = [e.semantic_id for e in entries]
        if len(ids) != len(set(ids)):
            duplicates = sorted({i for i in ids if ids.count(i) > 1})
            raise ValueError(f"duplicate semantic_id entries in profile: {duplicates}")
        return entries

    def count_by_state(self, state: LifecycleState) -> int:
        """Count extensions in a given lifecycle state."""
        return sum(1 for e in self.entries if e.state == state)

    def transition_to(self, to_state: LifecycleState) -> "Profile":
        """Return a copy with every entry moved to to_state (all must allow it)."""
        for entry in self.entries:
            assert_transition_allowed(entry.state, to_state)
        return Profile(
            version=self.version,
            entries=[
                ProfileEntry(
                    semantic_id=e.semantic_id,
                    state=to_state,
                    provider_binding=e.provider_binding,
                    activated_at=e.activated_at,
                    active_ttl_seconds=e.active_ttl_seconds,
                )
                for e in self.entries
            ],
        )

    def expire_stale(self, now: Optional[datetime] = None) -> "Profile":
        """Return a copy with expired RESIDENT entries demoted to AVAILABLE."""
        at = now or datetime.now(timezone.utc)
        expired_entries = []
        for entry in self.entries:
            if _is_expired(entry, at):
                expired_entries.append(
                    ProfileEntry(
                        semantic_id=entry.semantic_id,
                        state=LifecycleState.AVAILABLE,
                        provider_binding=entry.provider_binding,
                        activated_at=None,
                        active_ttl_seconds=entry.active_ttl_seconds,
                    )
                )
            else:
                expired_entries.append(entry)
        return Profile(version=self.version, entries=expired_entries)


SUPPLY_STATES = frozenset({LifecycleState.AVAILABLE, LifecycleState.STAGED})


def phase_of(state: LifecycleState) -> str:
    """Map a lifecycle state to its footprint phase: supply vs task."""
    return "supply" if state in SUPPLY_STATES else "task"


def is_selectable(entry: ProfileEntry) -> bool:
    """An entry is selectable unless it is DEGRADED (failed/fallback)."""
    return entry.state is not LifecycleState.DEGRADED


def assert_transition_allowed(from_state: LifecycleState, to_state: LifecycleState) -> None:
    """Raise with repair guidance unless from_state → to_state is valid."""
    if not validate_state_transition(from_state, to_state):
        allowed = sorted(s.value for s in VALID_TRANSITIONS.get(from_state, set()))
        raise ValueError(
            f"invalid lifecycle transition {from_state.value} → {to_state.value}; "
            f"allowed from {from_state.value}: {allowed or ['(none — terminal state)']}"
        )


def assert_can_invoke(entry: ProfileEntry) -> None:
    """Raise unless the entry is in RESIDENT (the only invocable phase)."""
    if entry.state is not LifecycleState.RESIDENT:
        raise ValueError(
            f"capability {entry.semantic_id} in wrong phase for invoke: "
            f"{entry.state.value} (must be RESIDENT)"
        )


def _is_expired(entry: ProfileEntry, now: datetime) -> bool:
    """A RESIDENT entry with activated_at + ttl in the past has expired."""
    if entry.state is not LifecycleState.RESIDENT:
        return False
    if not entry.activated_at or not entry.active_ttl_seconds:
        return False
    try:
        activated = datetime.fromisoformat(entry.activated_at.replace("Z", "+00:00"))
    except ValueError:
        return False
    if activated.tzinfo is None:
        activated = activated.replace(tzinfo=timezone.utc)
    return (activated.timestamp() + entry.active_ttl_seconds) <= now.timestamp()
