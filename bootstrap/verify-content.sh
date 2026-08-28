#!/usr/bin/env bash
# Verifies every vendored skill has a SKILL.md whose frontmatter `name`
# matches its folder name, and that LICENSE + VENDORED-FROM exist.
set -euo pipefail

REPO_ROOT="${1:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
failures=()

if [ ! -f "$REPO_ROOT/LICENSE" ]; then
  failures+=("Missing $REPO_ROOT/LICENSE")
fi

declare -A expected
expected["anthropic-product-skills"]="canvas-design web-artifacts-builder"
expected["general-skills"]="skill-creator"

for plugin in "${!expected[@]}"; do
  skills_dir="$REPO_ROOT/plugins/$plugin/skills"
  vendored_from="$skills_dir/VENDORED-FROM"

  if [ ! -f "$vendored_from" ]; then
    failures+=("Missing $vendored_from")
  fi

  for skill in ${expected[$plugin]}; do
    skill_md="$skills_dir/$skill/SKILL.md"
    if [ ! -f "$skill_md" ]; then
      failures+=("Missing $skill_md")
      continue
    fi
    found_name="$(awk 'NR==1{next} /^---[[:space:]]*$/{exit} /^name:/{sub(/^name:[[:space:]]*/,""); print; exit}' "$skill_md")"
    if [ -z "$found_name" ]; then
      failures+=("$skill_md has no frontmatter 'name' field")
      continue
    fi
    if [ "$found_name" != "$skill" ]; then
      failures+=("$skill_md frontmatter name '$found_name' does not match folder name '$skill'")
    fi
  done
done

if [ ${#failures[@]} -gt 0 ]; then
  echo "FAIL: content verification"
  printf '  - %s\n' "${failures[@]}"
  exit 1
fi

echo "PASS: all vendored skills present, named correctly, LICENSE and VENDORED-FROM present"
exit 0
