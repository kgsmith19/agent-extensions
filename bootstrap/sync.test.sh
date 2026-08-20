#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SCRATCH="$(mktemp -d)"
CODEX_DIR="$SCRATCH/agents-skills"
ANTIGRAVITY_DIR="$SCRATCH/gemini-plugins"
mkdir -p "$CODEX_DIR" "$ANTIGRAVITY_DIR"

# shellcheck source=/dev/null
source "$REPO_ROOT/bootstrap/sync.sh" --import

failures=()

sync_codex_skills "$REPO_ROOT" "$CODEX_DIR"
for skill in canvas-design web-artifacts-builder skill-creator; do
  if [ ! -e "$CODEX_DIR/$skill" ]; then
    failures+=("Codex: $CODEX_DIR/$skill does not exist")
  elif [ ! -L "$CODEX_DIR/$skill" ]; then
    failures+=("Codex: $CODEX_DIR/$skill exists but is not a symlink")
  fi
done

# Idempotency
sync_codex_skills "$REPO_ROOT" "$CODEX_DIR"
if [ ! -L "$CODEX_DIR/skill-creator" ]; then
  failures+=("Codex: second sync run broke the skill-creator link")
fi

sync_antigravity_plugins "$REPO_ROOT" "$ANTIGRAVITY_DIR"
for plugin in anthropic-product-skills general-skills; do
  if [ ! -e "$ANTIGRAVITY_DIR/$plugin" ]; then
    failures+=("Antigravity: $ANTIGRAVITY_DIR/$plugin does not exist")
  elif [ ! -L "$ANTIGRAVITY_DIR/$plugin" ]; then
    failures+=("Antigravity: $ANTIGRAVITY_DIR/$plugin exists but is not a symlink")
  fi
done

# Live-link requirement
MARKER="sync-test-marker-$$"
TEST_FILE="$REPO_ROOT/plugins/general-skills/skills/skill-creator/SKILL.md"
echo "<!-- $MARKER -->" >> "$TEST_FILE"
if ! grep -q "$MARKER" "$CODEX_DIR/skill-creator/SKILL.md"; then
  failures+=("Codex link for skill-creator is a copy, not a live link")
fi
sed -i "/$MARKER/d" "$TEST_FILE"

rm -rf "$SCRATCH"

if [ ${#failures[@]} -gt 0 ]; then
  echo "FAIL: sync.sh Codex/Antigravity linking"
  printf '  - %s\n' "${failures[@]}"
  exit 1
fi

echo "PASS: Codex and Antigravity linking, idempotent, live-linked"
exit 0
