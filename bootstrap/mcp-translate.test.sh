# bootstrap/mcp-translate.test.sh
#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=/dev/null
source "$REPO_ROOT/bootstrap/sync.sh" --import

failures=()

STDIO_JSON='{ "fixture-stdio": { "command": "node", "args": ["server.js", "--port", "0"], "env": { "FIXTURE_MODE": "stdio" } } }'
STDIO_TOML="$(mcp_json_to_codex_toml "$STDIO_JSON")"
echo "$STDIO_TOML" | grep -qF '[mcp_servers.fixture-stdio]' || failures+=("stdio: missing table header")
echo "$STDIO_TOML" | grep -qF 'command = "node"' || failures+=("stdio: missing/incorrect command")
echo "$STDIO_TOML" | grep -qF 'args = ["server.js","--port","0"]' || failures+=("stdio: missing/incorrect args")
echo "$STDIO_TOML" | grep -qF 'env = { FIXTURE_MODE = "stdio" }' || failures+=("stdio: missing/incorrect env")

HTTP_JSON='{ "fixture-http": { "url": "https://fixture.example.com/mcp", "headers": { "Authorization": "Bearer FIXTURE_TOKEN" } } }'
HTTP_TOML="$(mcp_json_to_codex_toml "$HTTP_JSON")"
echo "$HTTP_TOML" | grep -qF '[mcp_servers.fixture-http]' || failures+=("http: missing table header")
echo "$HTTP_TOML" | grep -qF 'url = "https://fixture.example.com/mcp"' || failures+=("http: missing/incorrect url")
echo "$HTTP_TOML" | grep -qF 'http_headers = { Authorization = "Bearer FIXTURE_TOKEN" }' || failures+=("http: missing/incorrect headers")

EMPTY_TOML="$(mcp_json_to_codex_toml '{}')"
if [ -n "$EMPTY_TOML" ]; then
  failures+=("empty: expected empty output for zero MCP servers")
fi

BAD_JSON='{ "fixture-bad": { "transportKind": "carrier-pigeon" } }'
if mcp_json_to_codex_toml "$BAD_JSON" 2>/dev/null; then
  failures+=("malformed: expected mcp_json_to_codex_toml to fail, it did not")
fi

if [ ${#failures[@]} -gt 0 ]; then
  echo "FAIL: mcp_json_to_codex_toml"
  printf '  - %s\n' "${failures[@]}"
  exit 1
fi

echo "PASS: mcp_json_to_codex_toml handles stdio, http, empty, and malformed input"
exit 0
