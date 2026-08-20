#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=/dev/null
source "$REPO_ROOT/bootstrap/sync.sh" --import

SCRATCH="$(mktemp -d)"
PLUGIN_DIR="$SCRATCH/fixture-http"

failures=()

JSON='{"mcpServers":{"fixture-http":{"url":"https://fixture.example.com/mcp"}}}'
write_antigravity_mcp_config "$PLUGIN_DIR" "$JSON"
WRITTEN="$(cat "$PLUGIN_DIR/mcp_config.json")"
[ "$(echo "$WRITTEN" | jq -r '.mcpServers."fixture-http".url')" = "https://fixture.example.com/mcp" ] \
  || failures+=("Written mcp_config.json does not round-trip the input JSON")

write_antigravity_mcp_config "$PLUGIN_DIR" "$JSON"
WRITTEN_AGAIN="$(cat "$PLUGIN_DIR/mcp_config.json")"
if [ "$WRITTEN_AGAIN" != "$WRITTEN" ]; then
  failures+=("Re-writing identical input changed the file (not idempotent)")
fi

EMPTY_DIR="$SCRATCH/no-mcp-plugin"
write_antigravity_mcp_config "$EMPTY_DIR" ""
if [ -f "$EMPTY_DIR/mcp_config.json" ]; then
  failures+=("Empty json_content should not create mcp_config.json")
fi

rm -rf "$SCRATCH"

if [ ${#failures[@]} -gt 0 ]; then
  echo "FAIL: write_antigravity_mcp_config"
  printf '  - %s\n' "${failures[@]}"
  exit 1
fi

echo "PASS: write_antigravity_mcp_config writes, is idempotent, no-ops on empty input"
exit 0
