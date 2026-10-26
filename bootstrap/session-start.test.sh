#!/usr/bin/env bash
# session-start.sh (#68): provision-when-needed, PATH once, queue head,
# never fails the session. Runs against a throwaway HOME with recording
# stubs for the provisioning and queue commands.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SCRIPT="$REPO_ROOT/bootstrap/session-start.sh"
SCRATCH="$(mktemp -d)"
trap 'rm -rf "$SCRATCH"' EXIT
failures=()

fresh_home() {
  rm -rf "$SCRATCH/home" "$SCRATCH/calls"
  mkdir -p "$SCRATCH/home"
  : > "$SCRATCH/calls"
}

run() {
  # run <env-file> [extra env...]; captures stdout+stderr and exit code
  local envfile="$1"; shift
  set +e
  OUT="$(env HOME="$SCRATCH/home" \
    AE_SESSION_PROVISION_CMD="echo provision >> '$SCRATCH/calls'" \
    AE_QUEUE_CMD="${QUEUE_CMD:-echo 'Q07 [pending] next thing'}" \
    "$@" bash "$SCRIPT" --env-file "$envfile" 2>&1)"
  CODE=$?
  set -e
}

# AC1: provisions on an empty HOME
fresh_home
run "$SCRATCH/env1"
[ "$(grep -c provision "$SCRATCH/calls" || true)" = "1" ] || failures+=("AC1: empty HOME did not provision exactly once")
echo "$OUT" | grep -q "provision: ok" || failures+=("AC1: missing 'provision: ok' report: $OUT")

# AC1: skips when skills and launcher are present
fresh_home
mkdir -p "$SCRATCH/home/.agents/skills/some-skill" "$SCRATCH/home/bin"
printf '#!/bin/sh\n' > "$SCRATCH/home/bin/ae"; chmod +x "$SCRATCH/home/bin/ae"
run "$SCRATCH/env2"
[ ! -s "$SCRATCH/calls" ] || failures+=("AC1: provisioned although skills and launcher were present")
echo "$OUT" | grep -q "provision: present" || failures+=("AC1: missing 'provision: present' report: $OUT")

# AC1: a missing launcher alone triggers provisioning
fresh_home
mkdir -p "$SCRATCH/home/.agents/skills/some-skill"
run "$SCRATCH/env3"
[ -s "$SCRATCH/calls" ] || failures+=("AC1: missing launcher did not trigger provisioning")

# AC2: PATH export appended exactly once across runs
fresh_home
envfile="$SCRATCH/env4"; : > "$envfile"
run "$envfile"; run "$envfile"
n="$(grep -c 'export PATH="$HOME/bin:$PATH"' "$envfile" || true)"
[ "$n" = "1" ] || failures+=("AC2: expected 1 PATH line in env file, found $n")

# AC3: queue head printed under the fixed header
fresh_home
run "$SCRATCH/env5"
echo "$OUT" | grep -q "== Execution queue (binding order) ==" || failures+=("AC3: queue header missing: $OUT")
echo "$OUT" | grep -q "Q07 \[pending\] next thing" || failures+=("AC3: queue head missing: $OUT")

# AC3 + AC4: failing queue command degrades, exit stays 0
fresh_home
QUEUE_CMD="echo boom >&2; exit 5" run "$SCRATCH/env6"
[ "$CODE" = "0" ] || failures+=("AC4: failing queue command changed exit code to $CODE")
echo "$OUT" | grep -q "queue: degraded" || failures+=("AC3: missing 'queue: degraded' notice: $OUT")

# AC4: failing provisioning degrades, exit stays 0
fresh_home
set +e
OUT="$(env HOME="$SCRATCH/home" AE_SESSION_PROVISION_CMD="exit 7" AE_QUEUE_CMD="true" \
  bash "$SCRIPT" --env-file "$SCRATCH/env7" 2>&1)"; CODE=$?
set -e
[ "$CODE" = "0" ] || failures+=("AC4: failing provisioning changed exit code to $CODE")
echo "$OUT" | grep -q "provision: degraded" || failures+=("AC4: missing 'provision: degraded' notice: $OUT")

# No env file given: still succeeds, nothing written
fresh_home
set +e
OUT="$(env HOME="$SCRATCH/home" AE_SESSION_PROVISION_CMD="true" AE_QUEUE_CMD="true" bash "$SCRIPT" 2>&1)"; CODE=$?
set -e
[ "$CODE" = "0" ] || failures+=("no env file: exit $CODE")
echo "$OUT" | grep -q "path: skipped" || failures+=("no env file: missing 'path: skipped': $OUT")

if [ ${#failures[@]} -gt 0 ]; then
  printf 'FAIL: %s\n' "${failures[@]}"
  exit 1
fi
echo "PASS: session-start provisions only when needed, adds PATH once, prints the queue head, never fails the session"
