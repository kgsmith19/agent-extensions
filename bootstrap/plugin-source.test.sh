#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=/dev/null
source "$REPO_ROOT/bootstrap/sync.sh" --import
# shellcheck source=/dev/null
source "$REPO_ROOT/bootstrap/tests/fixtures/marketplace-fixture.sh"

failures=()
scratch="$(mktemp -d)"
cleanup() { rm -rf "$scratch"; }
trap cleanup EXIT

repo_dir="$scratch/plugin-repo"
repo_sha="$(new_fixture_plugin_repo "$repo_dir")"
mp_dir="$scratch/marketplace"
new_fixture_marketplace "$mp_dir" "$repo_dir" "$repo_sha" >/dev/null

# get_declared_plugins: plain string entries
mp_strings='{"name":"m","plugins":["a","b"]}'
mapfile -t got < <(get_declared_plugins "$mp_strings")
if [ "${#got[@]}" -ne 2 ]; then
  failures+=("string form: expected 2 plugins, got ${#got[@]}")
fi
name0="$(echo "${got[0]}" | jq -r '.name' | tr -d '\r')"
rc0="$(echo "${got[0]}" | jq -r '.resolvedCommit' | tr -d '\r')"
if [ "$name0" != "a" ]; then failures+=("string form: expected name 'a', got '$name0'"); fi
if [ "$rc0" != "" ]; then failures+=("string form: resolvedCommit should be empty"); fi

# get_declared_plugins: object entries, and a mixed array
mp_objects='{"name":"m","plugins":[{"name":"c","resolvedCommit":"abc123"},"d"]}'
mapfile -t got2 < <(get_declared_plugins "$mp_objects")
name0="$(echo "${got2[0]}" | jq -r '.name' | tr -d '\r')"
rc0="$(echo "${got2[0]}" | jq -r '.resolvedCommit' | tr -d '\r')"
name1="$(echo "${got2[1]}" | jq -r '.name' | tr -d '\r')"
rc1="$(echo "${got2[1]}" | jq -r '.resolvedCommit' | tr -d '\r')"
if [ "$name0" != "c" ]; then failures+=("object form: expected name 'c'"); fi
if [ "$rc0" != "abc123" ]; then failures+=("object form: expected resolvedCommit 'abc123'"); fi
if [ "$name1" != "d" ]; then failures+=("mixed form: expected name 'd'"); fi
if [ "$rc1" != "" ]; then failures+=("mixed form: 'd' resolvedCommit should be empty"); fi

# get_declared_plugins: malformed JSON still returns 0
malformed_json='{"name":"m","plugins":['
if malformed_output="$(get_declared_plugins "$malformed_json" 2>&1)"; then
  malformed_status=0
else
  malformed_status=$?
fi
malformed_output="$(printf '%s' "$malformed_output" | tr -d '\r')"
if [ "$malformed_status" -ne 0 ]; then failures+=("malformed json: expected exit 0, got '$malformed_status'"); fi
if [ -n "$malformed_output" ]; then failures+=("malformed json: expected no output, got '$malformed_output'"); fi

# inline under plugins/
s="$(get_plugin_source "$mp_dir" "alpha-skills")"
kind="$(echo "$s" | jq -r '.kind' | tr -d '\r')"
path="$(echo "$s" | jq -r '.path' | tr -d '\r')"
if [ "$kind" != "inline" ]; then failures+=("alpha: expected kind inline, got '$kind'"); fi
if [ "$path" != "./plugins/alpha-skills" ]; then failures+=("alpha: wrong path '$path'"); fi

# inline under external_plugins/
s="$(get_plugin_source "$mp_dir" "gamma-mcp-http")"
kind="$(echo "$s" | jq -r '.kind' | tr -d '\r')"
path="$(echo "$s" | jq -r '.path' | tr -d '\r')"
if [ "$kind" != "inline" ]; then failures+=("gamma: expected kind inline"); fi
if [ "$path" != "./external_plugins/gamma-mcp-http" ]; then failures+=("gamma: wrong path '$path'"); fi

# external repo pinned by sha
s="$(get_plugin_source "$mp_dir" "zeta-repo-pinned")"
kind="$(echo "$s" | jq -r '.kind' | tr -d '\r')"
sha="$(echo "$s" | jq -r '.sha' | tr -d '\r')"
subpath="$(echo "$s" | jq -r '.subpath' | tr -d '\r')"
url="$(echo "$s" | jq -r '.url' | tr -d '\r')"
if [ "$kind" != "repo" ]; then failures+=("zeta: expected kind repo, got '$kind'"); fi
if [ "$sha" != "$repo_sha" ]; then failures+=("zeta: expected sha '$repo_sha', got '$sha'"); fi
if [ "$subpath" != "" ]; then failures+=("zeta: subpath should be empty"); fi
case "$url" in
  file:///*) ;;
  *) failures+=("zeta: expected file:/// url, got '$url'") ;;
esac

# external repo with a subdirectory
s="$(get_plugin_source "$mp_dir" "eta-repo-subpath")"
kind="$(echo "$s" | jq -r '.kind' | tr -d '\r')"
subpath="$(echo "$s" | jq -r '.subpath' | tr -d '\r')"
if [ "$kind" != "repo" ]; then failures+=("eta: expected kind repo"); fi
if [ "$subpath" != "nested/eta" ]; then failures+=("eta: expected subpath 'nested/eta', got '$subpath'"); fi

# external repo, unpinned
s="$(get_plugin_source "$mp_dir" "theta-repo-unpinned")"
kind="$(echo "$s" | jq -r '.kind' | tr -d '\r')"
sha="$(echo "$s" | jq -r '.sha' | tr -d '\r')"
ref="$(echo "$s" | jq -r '.ref' | tr -d '\r')"
if [ "$kind" != "repo" ]; then failures+=("theta: expected kind repo"); fi
if [ "$sha" != "" ]; then failures+=("theta: sha should be empty, got '$sha'"); fi
if [ "$ref" != "" ]; then failures+=("theta: ref should be empty, got '$ref'"); fi

# declared by us but absent from the manifest
s="$(get_plugin_source "$mp_dir" "omega-absent")"
kind="$(echo "$s" | jq -r '.kind' | tr -d '\r')"
error="$(echo "$s" | jq -r '.error' | tr -d '\r')"
if [ "$kind" != "missing" ]; then failures+=("omega: expected kind missing, got '$kind'"); fi
case "$error" in
  *omega-absent*) ;;
  *) failures+=("omega: error must name the plugin, got '$error'") ;;
esac

# marketplace clone with no manifest at all
bare="$scratch/bare"
mkdir -p "$bare"
s="$(get_plugin_source "$bare" "anything")"
kind="$(echo "$s" | jq -r '.kind' | tr -d '\r')"
error="$(echo "$s" | jq -r '.error' | tr -d '\r')"
if [ "$kind" != "missing" ]; then failures+=("bare: expected kind missing"); fi
case "$error" in
  *marketplace.json*) ;;
  *) failures+=("bare: error must name marketplace.json, got '$error'") ;;
esac

if [ ${#failures[@]} -gt 0 ]; then
  echo "FAIL: plugin source resolution"
  printf '  - %s\n' "${failures[@]}"
  exit 1
fi

echo "PASS: get_declared_plugins and get_plugin_source handle all source kinds and both missing cases"
exit 0
