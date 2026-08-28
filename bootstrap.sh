#!/usr/bin/env bash
# One-line bootstrap: fetch agent-extensions onto a machine holding no prior
# state, then run its declarative sync. Works two ways:
#
#   Already have a checkout:
#     ./bootstrap.sh
#
#   Bare machine, nothing cloned yet (repo must be public for this):
#     curl -fsSL https://raw.githubusercontent.com/kgsmith19/agent-extensions/main/bootstrap.sh | bash
#
# Every stage sync.sh runs is capability-gated — it detects which surfaces
# exist on this machine (a `claude` CLI, a `~/.codex`, a `~/.gemini`) and
# skips the rest with a stated reason. Nothing about this script is
# cloud-special-cased: it is the same clone-then-sync path everywhere,
# just detecting fewer surfaces on a thinner machine.
set -euo pipefail

REPO_URL="${AGENT_EXTENSIONS_REPO_URL:-https://github.com/kgsmith19/agent-extensions.git}"
INSTALL_DIR="${AGENT_EXTENSIONS_DIR:-$HOME/.agent-extensions}"

self_dir="$(dirname "${BASH_SOURCE[0]}")"
if [ -f "$self_dir/bootstrap/sync.sh" ]; then
  # Running from within an existing checkout — use it directly, no clone.
  exec "$self_dir/bootstrap/sync.sh" "$@"
fi

if [ -d "$INSTALL_DIR/.git" ]; then
  echo "Updating existing checkout at '$INSTALL_DIR'..."
  git -C "$INSTALL_DIR" pull --ff-only
else
  echo "Cloning agent-extensions into '$INSTALL_DIR'..."
  git clone --depth 1 "$REPO_URL" "$INSTALL_DIR"
fi

exec "$INSTALL_DIR/bootstrap/sync.sh" "$@"
