# bootstrap/vendor-cache.test.sh
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
mkdir -p "$DECLARE_ROOT/bootstrap"

REPO_DIR="$SCRATCH/plugin-repo"
REPO_SHA="$(new_fixture_plugin_repo "$REPO_DIR")"
SHA="$(new_fixture_marketplace "$FIXTURE_REPO" "$REPO_DIR" "$REPO_SHA")"

cat > "$DECLARE_ROOT/bootstrap/external-marketplaces.json" <<EOF
{
  "marketplaces": [
    { "name": "fixture-mp", "repo": "$FIXTURE_REPO", "pinnedCommit": "$SHA", "plugins": ["alpha-skills"] }
  ]
}
EOF

failures=()

# --- resolve_marketplace_url ---
if [ "$(resolve_marketplace_url "anthropics/claude-plugins-official")" != "https://github.com/anthropics/claude-plugins-official.git" ]; then
  failures+=("resolve_marketplace_url did not expand owner/repo shorthand correctly")
fi
if [ "$(resolve_marketplace_url "$FIXTURE_REPO")" != "$FIXTURE_REPO" ]; then
  failures+=("resolve_marketplace_url should pass through a local path unchanged")
fi

# --- get_external_marketplaces_json ---
COUNT="$(get_external_marketplaces_json "$DECLARE_ROOT" | wc -l | tr -d ' ')"
if [ "$COUNT" != "1" ]; then
  failures+=("get_external_marketplaces_json did not parse exactly 1 declared marketplace, got $COUNT")
fi

# --- sync_vendor_cache: initial clone ---
if ! sync_vendor_cache "$DECLARE_ROOT" "$VENDOR_CACHE"; then
  failures+=("sync_vendor_cache reported failure on a valid fixture")
fi
CLONED_FILE="$VENDOR_CACHE/fixture-mp/alpha-skills/skills/greet/SKILL.md"
if [ ! -f "$CLONED_FILE" ]; then
  failures+=("Vendor cache clone did not produce expected file: $CLONED_FILE")
fi

# --- Idempotency ---
MARKER="$VENDOR_CACHE/fixture-mp/MARKER.txt"
echo "should survive a no-op re-sync" > "$MARKER"
sync_vendor_cache "$DECLARE_ROOT" "$VENDOR_CACHE" || true
if [ ! -f "$MARKER" ]; then
  failures+=("sync_vendor_cache re-cloned an already-pinned marketplace (marker file was wiped)")
fi

# --- Missing pinned commit: loud, reported failure ---
cat > "$DECLARE_ROOT/bootstrap/external-marketplaces.json" <<EOF
{
  "marketplaces": [
    { "name": "fixture-mp", "repo": "$FIXTURE_REPO", "pinnedCommit": "0000000000000000000000000000000000000bad", "plugins": ["alpha-skills"] }
  ]
}
EOF
BAD_VENDOR_CACHE="$SCRATCH/vendor-cache-bad"
if sync_vendor_cache "$DECLARE_ROOT" "$BAD_VENDOR_CACHE" 2>/dev/null; then
  failures+=("sync_vendor_cache silently succeeded on a nonexistent pinned commit")
fi

rm -rf "$SCRATCH"

if [ ${#failures[@]} -gt 0 ]; then
  echo "FAIL: vendor-cache clone/pin"
  printf '  - %s\n' "${failures[@]}"
  exit 1
fi

echo "PASS: vendor-cache clone/pin, idempotent, loud on bad pin"
exit 0
