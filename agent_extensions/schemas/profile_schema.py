"""Profile schema with lifecycle state machine for agent extensions.

Defines lifecycle states, valid transitions, and the Profile aggregate:
AVAILABLE → STAGED → ENABLED → RESIDENT → INVOKED → {VERIFIED, DEGRADED}
"""

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

    @field_validator("semantic_id")
    @classmethod
    def validate_semantic_id(cls, v):
        if not v.startswith("capability."):
            raise ValueError("semantic_id must start with 'capability.'")
        return v


class Profile(BaseModel):
    """Active/resident profile — selected extensions for current task."""

    entries: List[ProfileEntry] = Field(
        default_factory=list, description="Selected extensions"
    )
    version: str = Field(default="1.0.0", description="Profile schema version")

    def count_by_state(self, state: LifecycleState) -> int:
        """Count extensions in a given lifecycle state."""
        return sum(1 for e in self.entries if e.state == state)
