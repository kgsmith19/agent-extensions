# bootstrap/codex-mcp-merge.test.sh
#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=/dev/null
source "$REPO_ROOT/bootstrap/sync.sh" --import

SCRATCH="$(mktemp -d)"
CONFIG_PATH="$SCRATCH/config.toml"

printf '[some_other_section]\nfoo = "bar"\n' > "$CONFIG_PATH"

failures=()

merge_codex_mcp_config "$CONFIG_PATH" \
  "beta-mcp-stdio" '[mcp_servers.fixture-stdio]
command = "node"' \
  "gamma-mcp-http" '[mcp_servers.fixture-http]
url = "https://fixture.example.com/mcp"'

AFTER_FIRST="$(cat "$CONFIG_PATH")"
echo "$AFTER_FIRST" | grep -qF '[some_other_section]' || failures+=("Merge dropped pre-existing unrelated content")
echo "$AFTER_FIRST" | grep -qF '[mcp_servers.fixture-stdio]' || failures+=("Merge did not add the stdio server table")
echo "$AFTER_FIRST" | grep -qF '[mcp_servers.fixture-http]' || failures+=("Merge did not add the http server table")

merge_codex_mcp_config "$CONFIG_PATH" \
  "beta-mcp-stdio" '[mcp_servers.fixture-stdio]
command = "node"' \
  "gamma-mcp-http" '[mcp_servers.fixture-http]
url = "https://fixture.example.com/mcp"'
AFTER_SECOND="$(cat "$CONFIG_PATH")"
if [ "$AFTER_SECOND" != "$AFTER_FIRST" ]; then
  failures+=("Re-merging identical input changed the file (not idempotent)")
fi

merge_codex_mcp_config "$CONFIG_PATH" \
  "beta-mcp-stdio" '[mcp_servers.fixture-stdio]
command = "node"'
AFTER_CHANGE="$(cat "$CONFIG_PATH")"
if echo "$AFTER_CHANGE" | grep -qF '[mcp_servers.fixture-http]'; then
  failures+=("Old managed content (fixture-http) was not removed after a changed merge")
fi
echo "$AFTER_CHANGE" | grep -qF '[some_other_section]' || failures+=("Unrelated content was lost after a changed merge")

rm -rf "$SCRATCH"

if [ ${#failures[@]} -gt 0 ]; then
  echo "FAIL: merge_codex_mcp_config"
  printf '  - %s\n' "${failures[@]}"
  exit 1
fi

echo "PASS: merge_codex_mcp_config preserves unrelated content, idempotent, replaces on change"
exit 0
