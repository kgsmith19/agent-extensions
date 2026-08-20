#!/usr/bin/env bash
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=/dev/null
source "$HERE/marketplace-fixture.sh"

SCRATCH="$(mktemp -d)"
SHA="$(new_fixture_marketplace "$SCRATCH")"

failures=()

if [[ ! "$SHA" =~ ^[0-9a-f]{40}$ ]]; then
  failures+=("new_fixture_marketplace did not return a 40-char commit SHA, got: '$SHA'")
fi

ACTUAL_SHA="$(git -C "$SCRATCH" rev-parse HEAD)"
if [ "$ACTUAL_SHA" != "$SHA" ]; then
  failures+=("Returned SHA '$SHA' does not match repo HEAD '$ACTUAL_SHA'")
fi

for expected in \
  "alpha-skills/skills/greet/SKILL.md" \
  "beta-mcp-stdio/.mcp.json" \
  "gamma-mcp-http/.mcp.json" \
  "delta-malformed/.mcp.json"; do
  if [ ! -f "$SCRATCH/$expected" ]; then
    failures+=("Expected fixture file missing: $expected")
  fi
done

rm -rf "$SCRATCH"

if [ ${#failures[@]} -gt 0 ]; then
  echo "FAIL: marketplace-fixture builder"
  printf '  - %s\n' "${failures[@]}"
  exit 1
fi

echo "PASS: marketplace-fixture builder produces expected content and commit"
exit 0
