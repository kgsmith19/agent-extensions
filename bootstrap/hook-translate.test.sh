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
if ! echo "$codex_json" | jq -e '.PreToolUse[0].hooks[0].command == "python3 \"/plugins/demo/hooks/pre.py\""' >/dev/null; then
  failures+=("Codex translation must rewrite \${CLAUDE_PLUGIN_ROOT} to the resolved absolute plugin dir")
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

rm -rf "$SCRATCH"

if [ ${#failures[@]} -gt 0 ]; then
  echo "FAIL: hook translation (Codex JSON / Antigravity JSON / merge)"
  printf '  - %s\n' "${failures[@]}"
  exit 1
fi

echo "PASS: hook translation — event filtering per target, path rewriting, field dropping, cross-plugin merge"
exit 0
