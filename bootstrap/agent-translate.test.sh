# bootstrap/agent-translate.test.sh
#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=/dev/null
source "$REPO_ROOT/bootstrap/sync.sh" --import

SCRATCH="$(mktemp -d)"
failures=()

# --- Simple case: single-line name/description translate cleanly ---
cat > "$SCRATCH/simple.md" <<'EOF'
---
name: simple-agent
description: Does a simple thing.
model: opus
tools: Read, Grep
color: red
---

You are a simple agent. Do the thing.
EOF

if ! toml="$(translate_agent_to_codex_toml "$SCRATCH/simple.md" "myplugin")"; then
  failures+=("Expected translate_agent_to_codex_toml to succeed on the simple case")
fi
if [ "$(echo "$toml" | grep '^name = ')" != 'name = "myplugin-simple-agent"' ]; then
  failures+=("Codex TOML name should be plugin-qualified, got: $(echo "$toml" | grep '^name = ')")
fi
if echo "$toml" | grep -qi '^model\|^tools'; then
  failures+=("Codex TOML must not carry over 'model' or 'tools' — no principled cross-provider mapping exists")
fi
if ! echo "$toml" | grep -q "You are a simple agent"; then
  failures+=("Codex TOML developer_instructions should contain the markdown body")
fi

if ! md="$(translate_agent_to_antigravity_md "$SCRATCH/simple.md" "myplugin")"; then
  failures+=("Expected translate_agent_to_antigravity_md to succeed on the simple case")
fi
if ! echo "$md" | grep -q '^name: "myplugin-simple-agent"'; then
  failures+=("Antigravity MD name should be plugin-qualified, got: $(echo "$md" | grep '^name:')")
fi
if echo "$md" | grep -qi '^model:\|^tools:\|^color:'; then
  failures+=("Antigravity MD must not carry over 'model', 'tools', or 'color'")
fi

# --- TOML validity: round-trip through Python's tomllib if available ---
if command -v python3 >/dev/null 2>&1; then
  echo "$toml" > "$SCRATCH/out.toml"
  if ! python3 -c "import tomllib; tomllib.load(open('$SCRATCH/out.toml','rb'))" 2>"$SCRATCH/tomlerr.txt"; then
    failures+=("Generated TOML failed to parse: $(cat "$SCRATCH/tomlerr.txt")")
  fi
fi

# --- No name field: defaults to filename ---
cat > "$SCRATCH/unnamed-agent.md" <<'EOF'
---
description: Has no name field.
---

Body text.
EOF
toml2="$(translate_agent_to_codex_toml "$SCRATCH/unnamed-agent.md" "myplugin")"
if [ "$(echo "$toml2" | grep '^name = ')" != 'name = "myplugin-unnamed-agent"' ]; then
  failures+=("Expected missing 'name:' to default to the filename, got: $(echo "$toml2" | grep '^name = ')")
fi

# --- Block-scalar description: declared failure, not a guess ---
cat > "$SCRATCH/block.md" <<'EOF'
---
name: block-agent
description: |
  This is a multi-line
  block scalar description.
---

Body.
EOF
STDERR_CAPTURE="$SCRATCH/stderr.txt"
REPORTED_OK=1
translate_agent_to_codex_toml "$SCRATCH/block.md" "myplugin" >/dev/null 2>"$STDERR_CAPTURE" || REPORTED_OK=0
if [ "$REPORTED_OK" = "1" ]; then
  failures+=("Expected translate_agent_to_codex_toml to decline a block-scalar description, not guess at it")
fi
if ! grep -q "not mechanically translatable" "$STDERR_CAPTURE"; then
  failures+=("Expected a clear 'not mechanically translatable' reason on stderr, got: $(cat "$STDERR_CAPTURE")")
fi

# --- Single-quoted description with embedded double quotes ---
cat > "$SCRATCH/quoted.md" <<'EOF'
---
name: quoted-agent
description: 'Say "hello" to the user, then continue.'
---

Body.
EOF
toml3="$(translate_agent_to_codex_toml "$SCRATCH/quoted.md" "myplugin")"
if ! echo "$toml3" | grep -q 'Say \\"hello\\" to the user'; then
  failures+=("Expected embedded double quotes to be preserved and properly escaped, got: $(echo "$toml3" | grep description)")
fi

# --- Collision safety: two different plugins with an agent of the same name ---
mkdir -p "$SCRATCH/plugin-a/agents" "$SCRATCH/plugin-b/agents"
cat > "$SCRATCH/plugin-a/agents/reviewer.md" <<'EOF'
---
name: reviewer
description: Plugin A's reviewer.
---
Body A.
EOF
cat > "$SCRATCH/plugin-b/agents/reviewer.md" <<'EOF'
---
name: reviewer
description: Plugin B's reviewer.
---
Body B.
EOF
codex_agents_out="$SCRATCH/codex-agents-out"
sync_plugin_agents_codex "$SCRATCH/plugin-a" "plugin-a" "$codex_agents_out"
sync_plugin_agents_codex "$SCRATCH/plugin-b" "plugin-b" "$codex_agents_out"
if [ ! -f "$codex_agents_out/plugin-a-reviewer.toml" ] || [ ! -f "$codex_agents_out/plugin-b-reviewer.toml" ]; then
  failures+=("Expected both plugins' same-named agent to produce distinct qualified files, got: $(ls "$codex_agents_out" 2>&1)")
fi

rm -rf "$SCRATCH"

if [ ${#failures[@]} -gt 0 ]; then
  echo "FAIL: agent translation (Codex TOML / Antigravity MD)"
  printf '  - %s\n' "${failures[@]}"
  exit 1
fi

echo "PASS: agent translation — simple case, name defaulting, block-scalar decline, quote handling, cross-plugin collision safety"
exit 0
