# bootstrap/sync-external-codex.test.sh
#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=/dev/null
source "$REPO_ROOT/bootstrap/sync.sh" --import
# shellcheck source=/dev/null
source "$REPO_ROOT/bootstrap/tests/fixtures/marketplace-fixture.sh"

SCRATCH="$(mktemp -d)"
FIXTURE_REPO="$SCRATCH/fixture-marketplace"
DECLARE_ROOT="$SCRATCH/declare-root"
VENDOR_CACHE="$SCRATCH/vendor-cache"
CODEX_SKILLS_DIR="$SCRATCH/agents-skills"
CODEX_CONFIG_PATH="$SCRATCH/codex-config.toml"
mkdir -p "$DECLARE_ROOT/bootstrap" "$CODEX_SKILLS_DIR"

SHA="$(new_fixture_marketplace "$FIXTURE_REPO")"
cat > "$DECLARE_ROOT/bootstrap/external-marketplaces.json" <<EOF
{
  "marketplaces": [
    { "name": "fixture-mp", "repo": "$FIXTURE_REPO", "pinnedCommit": "$SHA",
      "plugins": ["alpha-skills", "beta-mcp-stdio", "gamma-mcp-http", "delta-malformed"] }
  ]
}
EOF
sync_vendor_cache "$DECLARE_ROOT" "$VENDOR_CACHE"

failures=()

STDERR_CAPTURE="$SCRATCH/stderr-capture.txt"
REPORTED_OK=1
sync_external_codex_content "$DECLARE_ROOT" "$VENDOR_CACHE" "$CODEX_SKILLS_DIR" "$CODEX_CONFIG_PATH" 2>"$STDERR_CAPTURE" || REPORTED_OK=0

if [ "$REPORTED_OK" = "1" ]; then
  failures+=("Expected sync_external_codex_content to return non-zero for delta-malformed, it returned 0")
fi
if ! grep -q "delta-malformed" "$STDERR_CAPTURE"; then
  failures+=("Expected a reported failure mentioning delta-malformed on stderr")
fi

if [ ! -L "$CODEX_SKILLS_DIR/greet" ]; then
  failures+=("alpha-skills: 'greet' skill was not linked (or is not a live link) despite delta-malformed's failure")
fi

CONFIG_CONTENT=""
[ -f "$CODEX_CONFIG_PATH" ] && CONFIG_CONTENT="$(cat "$CODEX_CONFIG_PATH")"
echo "$CONFIG_CONTENT" | grep -qF '[mcp_servers.fixture-stdio]' || failures+=("beta-mcp-stdio's server was not merged into config.toml")
echo "$CONFIG_CONTENT" | grep -qF '[mcp_servers.fixture-http]' || failures+=("gamma-mcp-http's server was not merged into config.toml")
if echo "$CONFIG_CONTENT" | grep -q 'fixture-bad'; then
  failures+=("delta-malformed's unrecognized server should not appear in config.toml at all")
fi

rm -rf "$SCRATCH"

if [ ${#failures[@]} -gt 0 ]; then
  echo "FAIL: sync_external_codex_content"
  printf '  - %s\n' "${failures[@]}"
  exit 1
fi

echo "PASS: sync_external_codex_content links skills, merges MCP, isolates one plugin's failure"
exit 0
