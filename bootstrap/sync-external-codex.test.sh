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

REPO_DIR="$SCRATCH/plugin-repo"
REPO_SHA="$(new_fixture_plugin_repo "$REPO_DIR")"
SHA="$(new_fixture_marketplace "$FIXTURE_REPO" "$REPO_DIR" "$REPO_SHA")"
cat > "$DECLARE_ROOT/bootstrap/external-marketplaces.json" <<EOF
{
  "marketplaces": [
    { "name": "fixture-mp", "repo": "$FIXTURE_REPO", "pinnedCommit": "$SHA",
      "plugins": ["alpha-skills", "beta-mcp-stdio", "gamma-mcp-http", "epsilon-invalid-json", "delta-malformed", "zeta-repo-pinned", "eta-repo-subpath", "omega-absent"] }
  ]
}
EOF
sync_vendor_cache "$DECLARE_ROOT" "$VENDOR_CACHE"

failures=()

STDERR_CAPTURE="$SCRATCH/stderr-capture.txt"
REPORTED_OK=1
sync_external_codex_content "$DECLARE_ROOT" "$VENDOR_CACHE" "$CODEX_SKILLS_DIR" "$CODEX_CONFIG_PATH" 2>"$STDERR_CAPTURE" || REPORTED_OK=0

if [ "$REPORTED_OK" = "1" ]; then
  failures+=("Expected sync_external_codex_content to return non-zero for delta-malformed/epsilon-invalid-json, it returned 0")
fi
if ! grep -q "delta-malformed" "$STDERR_CAPTURE"; then
  failures+=("Expected a reported failure mentioning delta-malformed on stderr")
fi
# --- Regression: syntactically-invalid .mcp.json (JSON parse error, not just a
# semantically-wrong shape) must be caught and reported per-plugin, not abort
# the whole function via errexit — and processing must continue past it to
# later plugins (delta-malformed, checked above, comes after epsilon in the
# declared plugin order).
if ! grep -q "epsilon-invalid-json" "$STDERR_CAPTURE"; then
  failures+=("Expected a reported failure mentioning epsilon-invalid-json (invalid JSON) on stderr")
fi

if [ ! -f "$CODEX_SKILLS_DIR/greet/SKILL.md" ]; then
  failures+=("alpha-skills: 'greet' skill was not reachable despite delta-malformed's failure")
fi
if [ ! -e "$CODEX_SKILLS_DIR/remote-greet" ]; then
  failures+=("zeta-repo-pinned's 'remote-greet' skill was not linked from its external repo")
fi
if [ ! -e "$CODEX_SKILLS_DIR/eta-greet" ]; then
  failures+=("eta-repo-subpath's 'eta-greet' skill was not linked from its repo subdirectory")
fi
if ! grep -q "omega-absent" "$STDERR_CAPTURE"; then
  failures+=("omega-absent is declared but not in the manifest; it must be reported as a failure")
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
