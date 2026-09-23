#!/usr/bin/env bash
# One-line bootstrap: fetch agent-extensions onto a machine holding no prior
# state, then run the full installer (machine stages + capability sync).
# Works two ways:
#
#   Already have a checkout:
#     ./bootstrap.sh
#
#   Bare machine, nothing cloned yet (repo must be public for this):
#     curl -fsSL https://raw.githubusercontent.com/kgsmith19/agent-extensions/main/bootstrap.sh | bash
#
# The installer is the canonical Python path (agent_extensions/install):
# global gitignore for local overlays, harness shims, continuity hooks, the
# `ae` launcher, then the capability-gated sync stages. Every stage is
# idempotent and reports applied/skipped/failed with a reason; nothing is
# cloud-special-cased — the same path detects fewer surfaces on a thinner
# machine. Requires python3; without it, falls back to capability-only sync.
set -euo pipefail

REPO_URL="${AGENT_EXTENSIONS_REPO_URL:-https://github.com/kgsmith19/agent-extensions.git}"
INSTALL_DIR="${AGENT_EXTENSIONS_DIR:-$HOME/.agent-extensions}"

self_dir="$(dirname "${BASH_SOURCE[0]}")"
if [ -f "$self_dir/agent_extensions/install/__main__.py" ]; then
  # Running from within an existing checkout — use it directly, no clone.
  if command -v python3 >/dev/null 2>&1; then
    PYTHONPATH="$self_dir${PYTHONPATH:+:$PYTHONPATH}" exec python3 -m agent_extensions.install bootstrap "$@"
  fi
  echo "python3 not found — capability-only fallback (shims/hooks/ae skipped)" >&2
  exec "$self_dir/bootstrap/sync.sh" "$@"
fi

if [ -d "$INSTALL_DIR/.git" ]; then
  echo "Updating existing checkout at '$INSTALL_DIR'..."
  git -C "$INSTALL_DIR" pull --ff-only
else
  echo "Cloning agent-extensions into '$INSTALL_DIR'..."
  git clone --depth 1 "$REPO_URL" "$INSTALL_DIR"
fi

if command -v python3 >/dev/null 2>&1; then
  PYTHONPATH="$INSTALL_DIR${PYTHONPATH:+:$PYTHONPATH}" exec python3 -m agent_extensions.install bootstrap "$@"
fi
echo "python3 not found — capability-only fallback (shims/hooks/ae skipped)" >&2
exec "$INSTALL_DIR/bootstrap/sync.sh" "$@"
