#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SCRATCH="$(mktemp -d)"
DECL_PATH="$SCRATCH/bootstrap/external-marketplaces.json"
mkdir -p "$SCRATCH/bootstrap"

# shellcheck source=/dev/null
source "$REPO_ROOT/bootstrap/sync.sh" --import

failures=()

cleanup() {
  rm -rf "$SCRATCH"
}
trap cleanup EXIT

cat > "$DECL_PATH" <<'JSON'
{
  "marketplaces": [
    { "name": "mk-one", "repo": "o/one", "pinnedCommit": "aaa", "plugins": ["p-one", "p-two"] },
    { "name": "mk-two", "repo": "o/two", "pinnedCommit": "bbb", "plugins": ["p-three"] }
  ]
}
JSON

sha="1111111111111111111111111111111111111111"
if ! save_resolved_commit "$SCRATCH" "mk-one" "p-two" "$sha" 2>"$SCRATCH/err.log"; then
  failures+=("unexpected error: $(<"$SCRATCH/err.log")")
fi

if ! jq -e --arg s "$sha" '.marketplaces[] | select(.name == "mk-one") | .plugins[] | select(type == "object" and .name == "p-two" and .resolvedCommit == $s)' "$DECL_PATH" >/dev/null; then
  failures+=("p-two was not converted to an object entry with the saved resolvedCommit")
fi

if ! jq -e '.marketplaces[] | select(.name == "mk-one") | .plugins[] | select(type == "string" and . == "p-one")' "$DECL_PATH" >/dev/null; then
  failures+=("p-one should remain an untouched string entry")
fi

if ! jq -e '.marketplaces[] | select(.name == "mk-one") | (.plugins | length) == 2 and .pinnedCommit == "aaa"' "$DECL_PATH" >/dev/null; then
  failures+=("mk-one should still declare exactly 2 plugins and preserve pinnedCommit")
fi

if ! jq -e '.marketplaces[] | select(.name == "mk-two") | (.plugins | length) == 1 and .plugins[0] == "p-three"' "$DECL_PATH" >/dev/null; then
  failures+=("mk-two must be untouched")
fi

sha2="2222222222222222222222222222222222222222"
if ! save_resolved_commit "$SCRATCH" "mk-one" "p-two" "$sha2" 2>"$SCRATCH/err.log"; then
  failures+=("second save errored: $(<"$SCRATCH/err.log")")
fi

if ! jq -e --arg s "$sha2" '.marketplaces[] | select(.name == "mk-one") | (.plugins | length) == 2 and ([.plugins[] | select(type == "object" and .name == "p-two" and .resolvedCommit == $s)] | length) == 1' "$DECL_PATH" >/dev/null; then
  failures+=("second save did not replace the sha without duplicating the entry")
fi

if save_resolved_commit "$SCRATCH" "nope" "p-two" "$sha" 2>"$SCRATCH/err.log"; then
  failures+=("unknown marketplace should be an error")
elif ! grep -q "marketplace 'nope'" "$SCRATCH/err.log"; then
  failures+=("unknown marketplace error message was not reported")
fi

if save_resolved_commit "$SCRATCH" "mk-one" "nope" "$sha" 2>"$SCRATCH/err.log"; then
  failures+=("unknown plugin should be an error")
elif ! grep -q "plugin 'nope'" "$SCRATCH/err.log"; then
  failures+=("unknown plugin error message was not reported")
fi

if [ ${#failures[@]} -gt 0 ]; then
  echo "FAIL: resolved-commit persistence"
  printf '  - %s\n' "${failures[@]}"
  exit 1
fi

echo "PASS: save_resolved_commit pins one plugin, preserves the rest, replaces on update, errors on unknown names"
