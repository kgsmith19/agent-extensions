# bootstrap/mcp-translate-antigravity.test.sh
#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=/dev/null
source "$REPO_ROOT/bootstrap/sync.sh" --import

failures=()

STDIO_JSON='{ "fixture-stdio": { "command": "node", "args": ["server.js", "--port", "0"], "env": { "FIXTURE_MODE": "stdio" } } }'
STDIO_CONFIG="$(mcp_json_to_antigravity_config "$STDIO_JSON")"
[ "$(echo "$STDIO_CONFIG" | jq -r '.mcpServers."fixture-stdio".command')" = "node" ] || failures+=("stdio: command not preserved")
[ "$(echo "$STDIO_CONFIG" | jq -c '.mcpServers."fixture-stdio".args')" = '["server.js","--port","0"]' ] || failures+=("stdio: args not preserved")
[ "$(echo "$STDIO_CONFIG" | jq -r '.mcpServers."fixture-stdio".env.FIXTURE_MODE')" = "stdio" ] || failures+=("stdio: env not preserved")

HTTP_JSON='{ "fixture-http": { "url": "https://fixture.example.com/mcp", "headers": { "Authorization": "Bearer FIXTURE_TOKEN" } } }'
HTTP_CONFIG="$(mcp_json_to_antigravity_config "$HTTP_JSON")"
[ "$(echo "$HTTP_CONFIG" | jq -r '.mcpServers."fixture-http".serverUrl')" = "https://fixture.example.com/mcp" ] || failures+=("http: url not translated to serverUrl (Antigravity's schema — url/httpUrl are documented as unsupported legacy fields)")
[ "$(echo "$HTTP_CONFIG" | jq 'has("mcpServers") and (.mcpServers."fixture-http" | has("url") | not)')" = "true" ] || failures+=("http: legacy url field must not remain alongside serverUrl")
[ "$(echo "$HTTP_CONFIG" | jq -r '.mcpServers."fixture-http".headers.Authorization')" = "Bearer FIXTURE_TOKEN" ] || failures+=("http: headers not preserved")

EMPTY_CONFIG="$(mcp_json_to_antigravity_config '{}')"
[ "$(echo "$EMPTY_CONFIG" | jq '.mcpServers | length')" = "0" ] || failures+=("empty: expected mcpServers to be an empty object")

BAD_JSON='{ "fixture-bad": { "transportKind": "carrier-pigeon" } }'
if mcp_json_to_antigravity_config "$BAD_JSON" 2>/dev/null; then
  failures+=("malformed: expected mcp_json_to_antigravity_config to fail, it did not")
fi

BOTH_JSON='{ "fixture-both": { "command": "node", "url": "https://example.com" } }'
if mcp_json_to_antigravity_config "$BOTH_JSON" 2>/dev/null; then
  failures+=("malformed (both): expected mcp_json_to_antigravity_config to fail, it did not")
fi

EXTRA_JSON='{ "fixture-extra": { "command": "node", "cwd": "/tmp" } }'
if mcp_json_to_antigravity_config "$EXTRA_JSON" 2>/dev/null; then
  failures+=("malformed (extra field): expected mcp_json_to_antigravity_config to fail, it did not")
fi

if [ ${#failures[@]} -gt 0 ]; then
  echo "FAIL: mcp_json_to_antigravity_config"
  printf '  - %s\n' "${failures[@]}"
  exit 1
fi

echo "PASS: mcp_json_to_antigravity_config handles stdio, http, empty, and malformed input"
exit 0
