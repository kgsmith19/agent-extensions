#!/usr/bin/env bash
# Protects the documented one-liner: `curl ... | bash` runs bootstrap.sh with
# the script on stdin, where BASH_SOURCE is unset and `set -u` is active.
# Also protects the checkout path: running the script as a file still uses the
# checkout it lives in and never clones.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SCRATCH="$(mktemp -d)"

failures=()

cleanup() {
  rm -rf "$SCRATCH"
}
trap cleanup EXIT

FAKE_BIN="$SCRATCH/bin"
GIT_MARKER="$SCRATCH/git-called"
mkdir -p "$FAKE_BIN"

# Minimal stand-in for the real installer: records that control reached it.
write_fake_installer() {
  local root="$1"
  mkdir -p "$root/agent_extensions/install"
  cat > "$root/agent_extensions/install/__main__.py" <<'PY'
import os
import sys

with open(os.environ["AE_TEST_MARKER"], "w", encoding="utf-8") as fh:
    fh.write(" ".join(sys.argv[1:]))
PY
  : > "$root/agent_extensions/__init__.py"
  : > "$root/agent_extensions/install/__init__.py"
}

# Records every git call so the checkout scenario can assert none happened.
cat > "$FAKE_BIN/git" <<SH
#!/usr/bin/env bash
echo "\$*" >> "$GIT_MARKER"
exit 0
SH
chmod +x "$FAKE_BIN/git"

# Scenario 1: piped to bash (the documented one-liner), existing install dir.
INSTALL_DIR="$SCRATCH/agent-extensions"
STDIN_MARKER="$SCRATCH/installer-ran-stdin"
mkdir -p "$INSTALL_DIR/.git"
write_fake_installer "$INSTALL_DIR"

set +e
OUTPUT="$(
  cd "$SCRATCH" &&
  AE_TEST_MARKER="$STDIN_MARKER" \
  AGENT_EXTENSIONS_DIR="$INSTALL_DIR" \
  PATH="$FAKE_BIN:$PATH" \
  bash -s < "$REPO_ROOT/bootstrap.sh" 2>&1
)"
STATUS=$?
set -e

if [ "$STATUS" -ne 0 ]; then
  failures+=("stdin invocation exited $STATUS: $OUTPUT")
fi
if [ ! -f "$STDIN_MARKER" ]; then
  failures+=("stdin invocation never reached the installer: $OUTPUT")
elif [ "$(cat "$STDIN_MARKER")" != "bootstrap" ]; then
  failures+=("stdin invocation passed unexpected installer arguments: $(cat "$STDIN_MARKER")")
fi

# Scenario 2: run as a file from a checkout — use it, do not clone or pull.
CHECKOUT="$SCRATCH/checkout"
CHECKOUT_MARKER="$SCRATCH/installer-ran-checkout"
mkdir -p "$CHECKOUT"
cp "$REPO_ROOT/bootstrap.sh" "$CHECKOUT/bootstrap.sh"
write_fake_installer "$CHECKOUT"
rm -f "$GIT_MARKER"

set +e
OUTPUT="$(
  cd "$SCRATCH" &&
  AE_TEST_MARKER="$CHECKOUT_MARKER" \
  PATH="$FAKE_BIN:$PATH" \
  env -u AGENT_EXTENSIONS_DIR bash "$CHECKOUT/bootstrap.sh" 2>&1
)"
STATUS=$?
set -e

if [ "$STATUS" -ne 0 ]; then
  failures+=("checkout invocation exited $STATUS: $OUTPUT")
fi
if [ ! -f "$CHECKOUT_MARKER" ]; then
  failures+=("checkout invocation never reached the installer: $OUTPUT")
elif [ "$(cat "$CHECKOUT_MARKER")" != "bootstrap" ]; then
  failures+=("checkout invocation passed unexpected installer arguments: $(cat "$CHECKOUT_MARKER")")
fi
if [ -f "$GIT_MARKER" ]; then
  failures+=("checkout invocation should not clone or pull; git calls: $(tr '\n' ';' < "$GIT_MARKER")")
fi

if [ "${#failures[@]}" -gt 0 ]; then
  printf 'FAIL: %s\n' "${failures[@]}"
  exit 1
fi
echo "ok: bootstrap.sh runs from stdin and still uses a checkout"
