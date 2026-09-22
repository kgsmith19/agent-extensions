"""Provider-neutral session continuity: capsules + a thin CLI."""

from agent_extensions.continuity.capsule import (
    CAPSULE_VERSION,
    DEFAULT_CAPSULE_DIR,
    LOCAL_OVERLAY_NAME,
    Capsule,
    capsule_path,
    detect_git,
    detect_harness,
    detect_model,
    project_key,
    read_local_overlay,
    refresh,
)

__all__ = [
    "CAPSULE_VERSION",
    "DEFAULT_CAPSULE_DIR",
    "LOCAL_OVERLAY_NAME",
    "Capsule",
    "capsule_path",
    "detect_git",
    "detect_harness",
    "detect_model",
    "project_key",
    "read_local_overlay",
    "refresh",
]
