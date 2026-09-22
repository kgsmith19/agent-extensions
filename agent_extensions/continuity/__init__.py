"""Provider-neutral session continuity: capsules + a thin CLI."""

from agent_extensions.continuity.capsule import (
    CAPSULE_VERSION,
    DEFAULT_CAPSULE_DIR,
    Capsule,
    capsule_path,
    detect_git,
    detect_harness,
    detect_model,
    project_key,
    refresh,
)

__all__ = [
    "CAPSULE_VERSION",
    "DEFAULT_CAPSULE_DIR",
    "Capsule",
    "capsule_path",
    "detect_git",
    "detect_harness",
    "detect_model",
    "project_key",
    "refresh",
]
