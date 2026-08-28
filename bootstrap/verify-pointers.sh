#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="${1:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
failures=()

agents_path="$REPO_ROOT/AGENTS.md"
if [ ! -f "$agents_path" ]; then
  failures+=("Missing $agents_path")
elif [ -z "$(tr -d '[:space:]' < "$agents_path")" ]; then
  failures+=("$agents_path is empty")
fi

claude_path="$REPO_ROOT/CLAUDE.md"
if [ ! -f "$claude_path" ]; then
  failures+=("Missing $claude_path")
elif ! grep -q "AGENTS\.md" "$claude_path"; then
  failures+=("$claude_path does not reference AGENTS.md")
fi

gemini_path="$REPO_ROOT/GEMINI.md"
if [ ! -f "$gemini_path" ]; then
  failures+=("Missing $gemini_path")
elif ! grep -q "AGENTS\.md" "$gemini_path"; then
  failures+=("$gemini_path does not reference AGENTS.md")
fi

readme_path="$REPO_ROOT/README.md"
if [ ! -f "$readme_path" ]; then
  failures+=("Missing $readme_path")
fi

if [ ${#failures[@]} -gt 0 ]; then
  echo "FAIL: pointer file verification"
  printf '  - %s\n' "${failures[@]}"
  exit 1
fi

echo "PASS: AGENTS.md, CLAUDE.md, GEMINI.md, README.md all present and correctly linked"
exit 0
