# bootstrap/sync-external-antigravity.test.sh
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
STAGED_DIR="$SCRATCH/staged"
ANTIGRAVITY_DIR="$SCRATCH/gemini-plugins"
ANTIGRAVITY_AGENTS_DIR="$SCRATCH/gemini-agents"
mkdir -p "$DECLARE_ROOT/bootstrap" "$ANTIGRAVITY_DIR"

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
sync_external_antigravity_content "$DECLARE_ROOT" "$VENDOR_CACHE" "$STAGED_DIR" "$ANTIGRAVITY_DIR" "$ANTIGRAVITY_AGENTS_DIR" 2>"$STDERR_CAPTURE" || REPORTED_OK=0
if [ "$REPORTED_OK" = "1" ]; then
  failures+=("Expected sync_external_antigravity_content to return non-zero for delta-malformed/epsilon-invalid-json, it returned 0")
fi
if ! grep -q "delta-malformed" "$STDERR_CAPTURE"; then
  failures+=("Expected a reported failure mentioning delta-malformed on stderr")
fi
# --- Regression: syntactically-invalid .mcp.json (JSON parse error, not just a
# semantically-wrong shape) must be caught and reported per-plugin, not trip
# errexit and abort the whole function — and processing must continue past it
# to later plugins (delta-malformed, checked above, comes after epsilon in the
# declared plugin order).
if ! grep -q "epsilon-invalid-json" "$STDERR_CAPTURE"; then
  failures+=("Expected a reported failure mentioning epsilon-invalid-json (invalid JSON) on stderr")
fi

if [ ! -L "$ANTIGRAVITY_DIR/alpha-skills" ]; then
  failures+=("alpha-skills was not linked into the Antigravity plugins dir")
fi
if [ ! -f "$ANTIGRAVITY_DIR/alpha-skills/skills/greet/SKILL.md" ]; then
  failures+=("alpha-skills' skill is not reachable through the Antigravity link")
fi
if [ ! -f "$ANTIGRAVITY_DIR/zeta-repo-pinned/skills/remote-greet/SKILL.md" ]; then
  failures+=("zeta-repo-pinned's 'remote-greet' skill was not staged from its external repo")
fi
if [ ! -f "$ANTIGRAVITY_DIR/eta-repo-subpath/skills/eta-greet/SKILL.md" ]; then
  failures+=("eta-repo-subpath's 'eta-greet' skill was not staged from its repo subdirectory")
fi

# --- plugin.json marker (required by Antigravity's real loader to
# recognize a directory as a plugin at all — see
# https://antigravity.google/docs/ide/plugins/) ---
ALPHA_PLUGIN_JSON="$ANTIGRAVITY_DIR/alpha-skills/plugin.json"
if [ ! -f "$ALPHA_PLUGIN_JSON" ]; then
  failures+=("alpha-skills: no plugin.json was staged — Antigravity's loader would not recognize this directory as a plugin")
elif [ "$(jq -r '.name' "$ALPHA_PLUGIN_JSON")" != "alpha-skills" ]; then
  failures+=("alpha-skills' plugin.json has the wrong 'name'")
fi
if ! grep -q "omega-absent" "$STDERR_CAPTURE"; then
  failures+=("omega-absent is declared but not in the manifest; it must be reported as a failure")
fi

BETA_CONFIG="$ANTIGRAVITY_DIR/beta-mcp-stdio/mcp_config.json"
if [ ! -f "$BETA_CONFIG" ]; then
  failures+=("beta-mcp-stdio's mcp_config.json was not generated/linked")
else
  [ "$(jq -r '.mcpServers."fixture-stdio".command' "$BETA_CONFIG")" = "node" ] \
    || failures+=("beta-mcp-stdio's mcp_config.json has wrong content")
fi

if [ -f "$ANTIGRAVITY_DIR/delta-malformed/mcp_config.json" ]; then
  failures+=("delta-malformed should not have produced an mcp_config.json")
fi

if [ -f "$ANTIGRAVITY_DIR/epsilon-invalid-json/mcp_config.json" ]; then
  failures+=("epsilon-invalid-json should not have produced an mcp_config.json")
fi

if [ -f "$VENDOR_CACHE/fixture-mp/beta-mcp-stdio/mcp_config.json" ]; then
  failures+=("Sync wrote a generated file into the pinned vendor-cache clone — it must only write to the staged dir")
fi

rm -rf "$SCRATCH"

if [ ${#failures[@]} -gt 0 ]; then
  echo "FAIL: sync_external_antigravity_content"
  printf '  - %s\n' "${failures[@]}"
  exit 1
fi

echo "PASS: sync_external_antigravity_content stages+links plugins, generates MCP config, isolates one plugin's failure, never mutates the clone"
exit 0
