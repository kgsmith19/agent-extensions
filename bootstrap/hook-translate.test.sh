# bootstrap/hook-translate.test.sh
#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=/dev/null
source "$REPO_ROOT/bootstrap/sync.sh" --import

SCRATCH="$(mktemp -d)"
failures=()

cat > "$SCRATCH/hooks.json" <<'EOF'
{
  "hooks": {
    "PreToolUse": [
      { "hooks": [{ "type": "command", "command": "python3 \"${CLAUDE_PLUGIN_ROOT}/hooks/pre.py\"", "timeout": 10 }] }
    ],
    "UserPromptSubmit": [
      { "hooks": [{ "type": "command", "command": "echo hi" }] }
    ],
    "SessionStart": [
      { "matcher": "startup", "hooks": [{ "type": "command", "command": "echo start", "shell": "bash", "async": false }] }
    ],
    "NotAnEvent": [
      { "hooks": [{ "type": "command", "command": "echo unknown" }] }
    ]
  }
}
EOF

# --- Codex: PreToolUse, UserPromptSubmit, SessionStart all overlap; NotAnEvent must be dropped ---
codex_json="$(translate_hooks_to_codex_json "$SCRATCH/hooks.json" "/plugins/demo")"
for event in PreToolUse UserPromptSubmit SessionStart; do
  if ! echo "$codex_json" | jq -e --arg e "$event" 'has($e)' >/dev/null; then
    failures+=("Codex translation dropped event '$event' which is in its own documented vocabulary")
  fi
done
if echo "$codex_json" | jq -e 'has("NotAnEvent")' >/dev/null; then
  failures+=("Codex translation must drop events not in Codex's documented vocabulary")
fi
expected_codex_cmd="python3 \"$HOOK_ENV_WRAPPER\" \"/plugins/demo\" \"/plugins/demo/hooks/pre.py\""
if ! echo "$codex_json" | jq -e --arg expected "$expected_codex_cmd" '.PreToolUse[0].hooks[0].command == $expected' >/dev/null; then
  failures+=("Codex translation must rewrite \${CLAUDE_PLUGIN_ROOT} to the resolved absolute plugin dir and wrap python3 commands for CLAUDE_PLUGIN_ROOT env propagation")
fi
if echo "$codex_json" | jq -e '.SessionStart[0].hooks[0] | has("shell") or has("async")' >/dev/null; then
  failures+=("Codex translation must drop fields (shell, async) that aren't in Codex's documented hook schema")
fi
if ! echo "$codex_json" | jq -e '.SessionStart[0].matcher == "startup"' >/dev/null; then
  failures+=("Codex translation must preserve the matcher field")
fi

# --- Antigravity: only PreToolUse/PostToolUse/Stop overlap; UserPromptSubmit/SessionStart have no equivalent ---
antigravity_json="$(translate_hooks_to_antigravity_json "$SCRATCH/hooks.json" "/plugins/demo" "demo-plugin")"
if ! echo "$antigravity_json" | jq -e '."demo-plugin" | has("PreToolUse")' >/dev/null; then
  failures+=("Antigravity translation should keep PreToolUse")
fi
if echo "$antigravity_json" | jq -e '."demo-plugin" | has("UserPromptSubmit") or has("SessionStart")' >/dev/null; then
  failures+=("Antigravity translation must not invent UserPromptSubmit/SessionStart support it doesn't document")
fi
expected_antigravity_cmd="python3 \"$HOOK_ENV_WRAPPER\" \"/plugins/demo\" \"/plugins/demo/hooks/pre.py\""
if ! echo "$antigravity_json" | jq -e --arg expected "$expected_antigravity_cmd" '."demo-plugin".PreToolUse[0].hooks[0].command == $expected' >/dev/null; then
  failures+=("Antigravity translation must also wrap python3 hook commands for CLAUDE_PLUGIN_ROOT env propagation")
fi

# --- A hooks.json with only non-overlapping Antigravity events produces no output ---
cat > "$SCRATCH/only-unsupported.json" <<'EOF'
{ "hooks": { "UserPromptSubmit": [{ "hooks": [{ "type": "command", "command": "echo hi" }] }] } }
EOF
empty_result="$(translate_hooks_to_antigravity_json "$SCRATCH/only-unsupported.json" "/plugins/demo" "demo-plugin")"
if [ -n "$empty_result" ]; then
  failures+=("Expected no output when nothing overlaps Antigravity's event vocabulary, got: $empty_result")
fi

# --- merge_codex_hooks_json: multiple plugins' arrays for the same event are unioned, not overwritten ---
plugin_a_json='{"PreToolUse":[{"hooks":[{"type":"command","command":"a"}]}]}'
plugin_b_json='{"PreToolUse":[{"hooks":[{"type":"command","command":"b"}]}],"Stop":[{"hooks":[{"type":"command","command":"c"}]}]}'
merged_path="$SCRATCH/merged-hooks.json"
merge_codex_hooks_json "$merged_path" "plugin-a" "$plugin_a_json" "plugin-b" "$plugin_b_json"
pretool_count="$(jq '.hooks.PreToolUse | length' "$merged_path")"
if [ "$pretool_count" != "2" ]; then
  failures+=("Expected merge_codex_hooks_json to union both plugins' PreToolUse arrays (2 entries), got $pretool_count")
fi
if ! jq -e '.hooks.Stop[0].hooks[0].command == "c"' "$merged_path" >/dev/null; then
  failures+=("Expected merge_codex_hooks_json to include plugin-b's Stop hook")
fi

# --- TOML/JSON validity of everything generated so far ---
if command -v python3 >/dev/null 2>&1; then
  if ! python3 -c "import json; json.load(open('$merged_path'))" 2>"$SCRATCH/err.txt"; then
    failures+=("Merged hooks.json is not valid JSON: $(cat "$SCRATCH/err.txt")")
  fi
fi

# --- hook_env_wrapper.py: proves the actual failure mode and the fix, not just the string rewrite ---
if command -v python3 >/dev/null 2>&1; then
  mkdir -p "$SCRATCH/wraptest/core" "$SCRATCH/wraptest/hooks"
  touch "$SCRATCH/wraptest/core/__init__.py"
  cat > "$SCRATCH/wraptest/core/helper.py" <<'EOF'
def greet():
    return "hello from core"
EOF
  cat > "$SCRATCH/wraptest/hooks/myhook.py" <<'EOF'
import os
import sys
PLUGIN_ROOT = os.environ.get('CLAUDE_PLUGIN_ROOT')
if PLUGIN_ROOT and PLUGIN_ROOT not in sys.path:
    sys.path.insert(0, PLUGIN_ROOT)
try:
    from core.helper import greet
except ImportError as e:
    print(f"IMPORT FAILED: {e}")
    sys.exit(0)
print(greet())
EOF

  # Without the fix (raw invocation, no CLAUDE_PLUGIN_ROOT in env): reproduces
  # the exact silent-no-op failure mode this wrapper exists to fix.
  raw_output="$(env -u CLAUDE_PLUGIN_ROOT python3 "$SCRATCH/wraptest/hooks/myhook.py" 2>&1)"
  if [[ "$raw_output" != IMPORT\ FAILED* ]]; then
    failures+=("Expected the unwrapped hook script to fail importing its sibling module without CLAUDE_PLUGIN_ROOT set (reproducing the bug) — got: $raw_output")
  fi

  # Through the wrapper, with the plugin dir passed explicitly: the import
  # succeeds because CLAUDE_PLUGIN_ROOT is now actually in the environment.
  wrapped_output="$(env -u CLAUDE_PLUGIN_ROOT python3 "$HOOK_ENV_WRAPPER" "$SCRATCH/wraptest" "$SCRATCH/wraptest/hooks/myhook.py" 2>&1)"
  if [ "$wrapped_output" != "hello from core" ]; then
    failures+=("Expected hook_env_wrapper.py to make CLAUDE_PLUGIN_ROOT available so the sibling-module import succeeds — got: $wrapped_output")
  fi
else
  echo "python3 not found — skipping hook_env_wrapper.py end-to-end test"
fi

# --- A non-python command is left untouched, not guessed at ---
cat > "$SCRATCH/nonpython-hooks.json" <<'EOF'
{ "hooks": { "PreToolUse": [{ "hooks": [{ "type": "command", "command": "node \"${CLAUDE_PLUGIN_ROOT}/hooks/pre.js\"" }] }] } }
EOF
nonpython_codex_json="$(translate_hooks_to_codex_json "$SCRATCH/nonpython-hooks.json" "/plugins/demo")"
if ! echo "$nonpython_codex_json" | jq -e '.PreToolUse[0].hooks[0].command == "node \"/plugins/demo/hooks/pre.js\""' >/dev/null; then
  failures+=("A non-python3 hook command must be left unwrapped (only the confirmed python3 \"<script>\" shape is rewritten)")
fi

rm -rf "$SCRATCH"

if [ ${#failures[@]} -gt 0 ]; then
  echo "FAIL: hook translation (Codex JSON / Antigravity JSON / merge)"
  printf '  - %s\n' "${failures[@]}"
  exit 1
fi

echo "PASS: hook translation — event filtering per target, path rewriting, field dropping, cross-plugin merge"
exit 0
