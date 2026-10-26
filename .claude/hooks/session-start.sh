#!/usr/bin/env bash
# Thin Claude Code adapter (#68). Cloud sessions only: runs the
# provider-neutral bootstrap/session-start.sh, then this repo's test deps
# (requirements-dev.txt in a venv, pinned pwsh 7.4.6) so the PR Gate's
# suites run locally. Idempotent; never fails the session.
set -uo pipefail
[ "${CLAUDE_CODE_REMOTE:-}" = "true" ] || exit 0

ROOT="${CLAUDE_PROJECT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"
ENV_FILE="${CLAUDE_ENV_FILE:-}"
bash "$ROOT/bootstrap/session-start.sh" --env-file "$ENV_FILE"

export_once() {
  [ -n "$ENV_FILE" ] || return 0
  grep -qxF "$1" "$ENV_FILE" 2>/dev/null || printf '%s\n' "$1" >> "$ENV_FILE"
}

# Python test deps (pinned) in a venv; system Python may be externally managed.
VENV="$HOME/.cache/agent-extensions/venv"
if [ ! -x "$VENV/bin/python" ]; then
  python3 -m venv "$VENV" || echo "deps: degraded (cannot create venv)"
fi
if "$VENV/bin/python" -m pip install -q --disable-pip-version-check -r "$ROOT/requirements-dev.txt"; then
  echo "deps: ok (requirements-dev.txt in $VENV)"
  export_once "export PATH=\"$VENV/bin:\$PATH\""
else
  echo "deps: degraded (pip install -r requirements-dev.txt failed)"
fi

# PowerShell 7 for the pwsh-backed tests, pinned by version and sha256.
PWSH_VERSION="7.4.6"
PWSH_SHA256="6f6015203c47806c5cc444c19d8ed019695e610fbd948154264bf9ca8e157561"
PWSH_DIR="$HOME/.local/opt/pwsh-$PWSH_VERSION"
if command -v pwsh >/dev/null 2>&1; then
  echo "pwsh: present ($(command -v pwsh))"
elif [ -x "$PWSH_DIR/pwsh" ]; then
  mkdir -p "$HOME/bin" && ln -sf "$PWSH_DIR/pwsh" "$HOME/bin/pwsh"
  echo "pwsh: present ($PWSH_DIR)"
elif [ "$(uname -s)-$(uname -m)" = "Linux-x86_64" ]; then
  tmp="$(mktemp -d)"
  url="https://github.com/PowerShell/PowerShell/releases/download/v$PWSH_VERSION/powershell-$PWSH_VERSION-linux-x64.tar.gz"
  if curl -fsSL --max-time 300 -o "$tmp/pwsh.tgz" "$url" \
     && echo "$PWSH_SHA256  $tmp/pwsh.tgz" | sha256sum -c --quiet - \
     && mkdir -p "$PWSH_DIR" "$HOME/bin" && tar xzf "$tmp/pwsh.tgz" -C "$PWSH_DIR" \
     && chmod +x "$PWSH_DIR/pwsh" && ln -sf "$PWSH_DIR/pwsh" "$HOME/bin/pwsh"; then
    echo "pwsh: ok ($PWSH_VERSION, sha256 verified)"
  else
    echo "pwsh: degraded (download, checksum or unpack failed)"
  fi
  rm -rf "$tmp"
else
  echo "pwsh: skipped (no pinned build for $(uname -s)-$(uname -m))"
fi
exit 0
