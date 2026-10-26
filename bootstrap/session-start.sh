#!/usr/bin/env bash
# Provider-neutral session start (#68). Any harness calls this at session
# start; harness adapters (e.g. .claude/hooks/session-start.sh) stay thin.
#
#   session-start.sh [--env-file FILE]
#
# 1. provision  — run the agent-extensions bootstrap only when the capability
#                 layer is absent (~/.agents/skills empty or ~/bin/<ae> missing)
# 2. path       — append `export PATH="$HOME/bin:$PATH"` to FILE once, so the
#                 harness exports it for the rest of the session
# 3. queue      — print the owner's execution-queue head (binding order)
#
# Every step reports ok / present / skipped / degraded and the script always
# exits 0: a missing network or tool degrades the session, never blocks it.
# Overrides (used by tests): AE_SESSION_PROVISION_CMD, AE_QUEUE_CMD.
set -uo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE=""
while [ $# -gt 0 ]; do
  case "$1" in
    --env-file) ENV_FILE="${2:-}"; shift 2 ;;
    *) echo "session-start: unknown argument '$1' (ignored)"; shift ;;
  esac
done

QUEUE_URL="https://raw.githubusercontent.com/kgsmith19/agent-engineering-standard/main/tools/work_queue.py"

default_queue_cmd() {
  # Prefer a local standard checkout; otherwise fetch the tool from main.
  local local_tool="${AES_STANDARD_DIR:-}/tools/work_queue.py"
  if [ -n "${AES_STANDARD_DIR:-}" ] && [ -f "$local_tool" ]; then
    python3 -I "$local_tool" next
    return
  fi
  local tmp
  tmp="$(mktemp -d)" || return 1
  curl -fsSL --max-time 20 -o "$tmp/work_queue.py" "$QUEUE_URL" \
    && python3 -I "$tmp/work_queue.py" next
  local rc=$?
  rm -rf "$tmp"
  return $rc
}

# 1. provision ---------------------------------------------------------------
launcher="$HOME/bin/${AE_COMMAND:-ae}"
if [ -n "$(ls -A "$HOME/.agents/skills" 2>/dev/null)" ] && [ -x "$launcher" ]; then
  echo "provision: present (~/.agents/skills and $launcher)"
else
  provision_out="$(bash -c "${AE_SESSION_PROVISION_CMD:-bash '$REPO_ROOT/bootstrap.sh'}" 2>&1)"
  rc=$?
  if [ $rc -eq 0 ]; then
    echo "provision: ok"
  else
    echo "provision: degraded (bootstrap exited $rc; last line: $(printf '%s\n' "$provision_out" | tail -n 1))"
  fi
fi

# 2. path --------------------------------------------------------------------
path_line='export PATH="$HOME/bin:$PATH"'
if [ -z "$ENV_FILE" ]; then
  echo "path: skipped (no --env-file)"
elif grep -qxF "$path_line" "$ENV_FILE" 2>/dev/null; then
  echo "path: ok (already exported)"
elif printf '%s\n' "$path_line" >> "$ENV_FILE" 2>/dev/null; then
  echo "path: ok (~/bin exported for this session)"
else
  echo "path: degraded (cannot write $ENV_FILE)"
fi

# 3. queue -------------------------------------------------------------------
echo "== Execution queue (binding order) =="
if [ -n "${AE_QUEUE_CMD:-}" ]; then
  queue_out="$(bash -c "$AE_QUEUE_CMD" 2>&1)"
else
  queue_out="$(default_queue_cmd 2>&1)"
fi
rc=$?
if [ $rc -eq 0 ] || [ $rc -eq 3 ]; then
  # 3 = QUEUE EMPTY (work_queue.py contract)
  printf '%s\n' "$queue_out"
  echo "Resume with: python3 tools/work_queue.py next (agent-engineering-standard)"
else
  echo "queue: degraded (exit $rc: $(printf '%s\n' "$queue_out" | tail -n 1))"
fi
exit 0
