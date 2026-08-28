#!/usr/bin/env bash
# One-shot, real-CLI verification that a repo-synced plugin's hook actually
# fires through Antigravity's own hook engine -- not just that hooks.json
# was generated correctly (verify-external.sh already proves that from a
# sandbox with no Antigravity installed) and not just that the hook script
# runs correctly when invoked by hand (proven in this repo's own test
# suite, and by direct testing against the real vendored hookify plugin in
# PR #2). This is the one thing nothing else in this repo can prove,
# because it requires a real, authenticated `agy` CLI session tied to your
# own Antigravity account -- headless mode explicitly documents that it
# "uses your cached credentials" from a prior interactive login, with no
# API-key or service-account path, so no cloud sandbox can do this step on
# your behalf. Run this on the machine where `agy` is already installed
# and authenticated (run `agy` once interactively first if you haven't).
#
# This script is designed against agy's documented headless interface
# (`agy -p "<prompt>" --output-format json`, antigravity.google/docs/cli/
# headless) but has NOT been run against a real agy session anywhere in
# this effort -- no environment involved had authenticated credentials.
# If the actual output shape differs from what's matched below, read the
# raw output printed on failure/INFO rather than trusting the verdict
# blindly, exactly the same discipline this whole effort has applied to
# every other report along the way.
#
# What it does: see bootstrap/verify-antigravity-live.ps1's header for the
# full six-step walkthrough (agy present/authenticated, plain tool
# execution not blocked, write a temporary hookify rule, ask agy to run
# the exact command it should block as a REAL tool call rather than a
# hand-invoked script, report whether it was actually blocked, clean up).
set -uo pipefail

failed=0

result() {
  local name="$1" status="$2" detail="$3"
  local marker="[INFO]"
  [ "$status" = "PASS" ] && marker="[PASS]"
  [ "$status" = "FAIL" ] && { marker="[FAIL]"; failed=1; }
  echo "$marker $name -- $detail"
}

# --- Step 1: agy present and authenticated ---
if ! command -v agy >/dev/null 2>&1; then
  result "cli-present" "FAIL" "agy not found on PATH. Install per https://antigravity.google/docs/cli/getting-started, then run 'agy' once interactively to authenticate, then re-run this script."
  exit 1
fi
result "cli-present" "PASS" "agy found at $(command -v agy)"

auth_probe="$(agy -p "reply with exactly: PONG" --output-format json 2>&1)"
auth_exit=$?
if [ $auth_exit -ne 0 ] || echo "$auth_probe" | grep -qi "authentication required"; then
  result "cli-authenticated" "FAIL" "agy is not authenticated in this shell. Run 'agy' interactively once, then re-run this script. Raw output: $auth_probe"
  exit 1
fi
result "cli-authenticated" "PASS" "headless probe responded"

# --- Step 2: tool execution isn't blocked by an unrelated built-in hook ---
tool_probe="$(agy -p "Run the shell command: echo AGENT_EXTENSIONS_TOOLCHECK" --output-format json 2>&1)"
if ! echo "$tool_probe" | grep -q "AGENT_EXTENSIONS_TOOLCHECK"; then
  result "tool-execution" "FAIL" "A plain, unblocked shell command did not appear to execute -- something (possibly the googlecloudtools.datacloud_telemetry built-in hook seen earlier in this effort) may still be blocking the tool-execution pipeline before any repo-synced hook gets a chance to run. Raw output: $tool_probe"
  exit 1
fi
result "tool-execution" "PASS" "a plain shell command executed and its output came back through agy"

# --- Step 3: write a temporary, distinctive hookify rule ---
mkdir -p .claude
rule_path=".claude/hookify.agent-extensions-live-check.local.md"
cat > "$rule_path" <<'EOF'
---
name: agent-extensions-live-check
enabled: true
event: bash
pattern: AGENT_EXTENSIONS_HOOK_MARKER_87234
action: block
---
Temporary rule written by bootstrap/verify-antigravity-live.sh to prove a
repo-synced hook fires through Antigravity's real hook engine. Safe to
delete by hand; this script removes it automatically when done.
EOF
cleanup() { rm -f "$rule_path"; }
trap cleanup EXIT

# --- Step 4: ask agy to run the exact command that rule should block, for real ---
block_probe="$(agy -p "Run this exact shell command and tell me exactly what happened, including any denial or error message verbatim: echo AGENT_EXTENSIONS_HOOK_MARKER_87234" --output-format json 2>&1)"

# --- Step 5: interpret the result ---
if echo "$block_probe" | grep -q "AGENT_EXTENSIONS_HOOK_MARKER_87234" && ! echo "$block_probe" | grep -qiE "denied|blocked|prevent"; then
  result "hook-fires-for-real" "FAIL" "The command that should have been blocked by hookify's rule appears to have run anyway. hookify's PreToolUse hook is NOT firing through Antigravity's real hook engine, even though it works correctly when invoked by hand (already proven in PR #2). Raw output: $block_probe"
elif echo "$block_probe" | grep -qiE "denied|blocked|prevent"; then
  result "hook-fires-for-real" "PASS" "The command was blocked -- hookify's PreToolUse hook fired for real through Antigravity's hook engine. This closes the one remaining open question in the whole agent-extensions completion effort."
else
  result "hook-fires-for-real" "INFO" "Inconclusive -- neither a clear block nor a clear pass-through was detected in the output. Read it directly rather than trusting this script's pattern-matching: $block_probe"
fi

exit $failed
