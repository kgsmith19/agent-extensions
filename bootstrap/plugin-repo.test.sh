#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SCRATCH="$(mktemp -d)"

# shellcheck source=/dev/null
source "$REPO_ROOT/bootstrap/sync.sh" --import
# shellcheck source=/dev/null
source "$REPO_ROOT/bootstrap/tests/fixtures/marketplace-fixture.sh"

failures=()

cleanup() {
  rm -rf "$SCRATCH"
}
trap cleanup EXIT

REPO_DIR="$SCRATCH/plugin-repo"
REPO_SHA="$(new_fixture_plugin_repo "$REPO_DIR")"
REPO_REF="$(git -C "$REPO_DIR" symbolic-ref --short HEAD)"
MP_DIR="$SCRATCH/marketplace"
new_fixture_marketplace "$MP_DIR" "$REPO_DIR" "$REPO_SHA" >/dev/null
REPOS_DIR="$SCRATCH/_plugins"

# pinned by manifest sha
SRC="$(get_plugin_source "$MP_DIR" "zeta-repo-pinned")"
R="$(sync_plugin_repo "$REPOS_DIR" "fx" "zeta-repo-pinned" "$SRC" "")"
ERR="$(echo "$R" | jq -r '.error')"
DIR="$(echo "$R" | jq -r '.dir')"
RESOLVED="$(echo "$R" | jq -r '.resolvedSha')"
if [ "$ERR" != "" ]; then
  failures+=("zeta: unexpected error '$ERR'")
fi
if [ "$RESOLVED" != "$REPO_SHA" ]; then
  failures+=("zeta: expected sha '$REPO_SHA', got '$RESOLVED'")
fi
if [ ! -f "$DIR/skills/remote-greet/SKILL.md" ]; then
  failures+=("zeta: root skill not present in clone")
fi

# subpath
SRC="$(get_plugin_source "$MP_DIR" "eta-repo-subpath")"
R="$(sync_plugin_repo "$REPOS_DIR" "fx" "eta-repo-subpath" "$SRC" "")"
ERR="$(echo "$R" | jq -r '.error')"
DIR="$(echo "$R" | jq -r '.dir')"
if [ "$ERR" != "" ]; then
  failures+=("eta: unexpected error '$ERR'")
fi
if [ ! -f "$DIR/skills/eta-greet/SKILL.md" ]; then
  failures+=("eta: Dir must point at the nested/eta subdirectory")
fi
if [[ ! "$DIR" =~ eta$ ]]; then
  failures+=("eta: Dir should end at the subpath, got '$DIR'")
fi

# unpinned resolves to HEAD and reports the sha it landed on
SRC="$(get_plugin_source "$MP_DIR" "theta-repo-unpinned")"
R="$(sync_plugin_repo "$REPOS_DIR" "fx" "theta-repo-unpinned" "$SRC" "")"
ERR="$(echo "$R" | jq -r '.error')"
RESOLVED="$(echo "$R" | jq -r '.resolvedSha')"
if [ "$ERR" != "" ]; then
  failures+=("theta: unexpected error '$ERR'")
fi
if [ "$RESOLVED" != "$REPO_SHA" ]; then
  failures+=("theta: expected HEAD sha '$REPO_SHA', got '$RESOLVED'")
fi

# idempotent: second call on an already-correct clone succeeds
R2="$(sync_plugin_repo "$REPOS_DIR" "fx" "zeta-repo-pinned" "$(get_plugin_source "$MP_DIR" "zeta-repo-pinned")" "")"
ERR="$(echo "$R2" | jq -r '.error')"
RESOLVED="$(echo "$R2" | jq -r '.resolvedSha')"
if [ "$ERR" != "" ]; then
  failures+=("zeta rerun: unexpected error '$ERR'")
fi
if [ "$RESOLVED" != "$REPO_SHA" ]; then
  failures+=("zeta rerun: sha changed")
fi

# ref-based source is idempotent when the clone already sits at the ref's commit
REF_SRC="$(jq -n --arg url "$(echo "$SRC" | jq -r '.url')" --arg ref "$REPO_REF" '{kind:"repo",path:"",url:$url,sha:"",ref:$ref,subpath:"",error:""}')"
REF_PLUGIN="theta-repo-ref"
REF1="$(sync_plugin_repo "$REPOS_DIR" "fx" "$REF_PLUGIN" "$REF_SRC" "")"
ERR="$(echo "$REF1" | jq -r '.error')"
DIR="$(echo "$REF1" | jq -r '.dir')"
RESOLVED="$(echo "$REF1" | jq -r '.resolvedSha')"
if [ "$ERR" != "" ]; then
  failures+=("theta ref: unexpected error '$ERR'")
fi
if [ "$RESOLVED" != "$REPO_SHA" ]; then
  failures+=("theta ref: expected ref sha '$REPO_SHA', got '$RESOLVED'")
fi
SENTINEL="$DIR/sentinel.txt"
printf '%s' "keep" > "$SENTINEL"
REF2="$(sync_plugin_repo "$REPOS_DIR" "fx" "$REF_PLUGIN" "$REF_SRC" "")"
ERR="$(echo "$REF2" | jq -r '.error')"
RESOLVED="$(echo "$REF2" | jq -r '.resolvedSha')"
if [ "$ERR" != "" ]; then
  failures+=("theta ref rerun: unexpected error '$ERR'")
fi
if [ "$RESOLVED" != "$REPO_SHA" ]; then
  failures+=("theta ref rerun: sha changed")
fi
if [ ! -f "$SENTINEL" ]; then
  failures+=("theta ref rerun: expected existing clone to remain in place")
fi

# PinnedCommit wins over the manifest sha
R3="$(sync_plugin_repo "$REPOS_DIR" "fx" "theta-repo-unpinned" "$SRC" "$REPO_SHA")"
ERR="$(echo "$R3" | jq -r '.error')"
RESOLVED="$(echo "$R3" | jq -r '.resolvedSha')"
if [ "$ERR" != "" ]; then
  failures+=("theta pinned: unexpected error '$ERR'")
fi
if [ "$RESOLVED" != "$REPO_SHA" ]; then
  failures+=("theta pinned: expected '$REPO_SHA'")
fi

# unreachable commit is a reported error, never a silent success
BOGUS="0123456789012345678901234567890123456789"
BAD_SRC="$(jq -n --arg url "$(echo "$SRC" | jq -r '.url')" --arg bogus "$BOGUS" '{kind:"repo",path:"",url:$url,sha:$bogus,ref:"",subpath:"",error:""}')"
R4="$(sync_plugin_repo "$REPOS_DIR" "fx" "bad-sha" "$BAD_SRC" "")"
ERR="$(echo "$R4" | jq -r '.error')"
DIR="$(echo "$R4" | jq -r '.dir')"
if [ "$ERR" = "" ]; then
  failures+=("bad-sha: expected a reported error, got none")
fi
if [ "$DIR" != "" ]; then
  failures+=("bad-sha: Dir must be empty when Error is set")
fi
if [[ "$ERR" != *"$BOGUS"* ]]; then
  failures+=("bad-sha: error must name the unreachable commit")
fi

# missing subpath is a reported error
SUB_SRC="$(jq -n --arg url "$(echo "$SRC" | jq -r '.url')" --arg sha "$REPO_SHA" '{kind:"repo",path:"",url:$url,sha:$sha,ref:"",subpath:"no/such/dir",error:""}')"
R5="$(sync_plugin_repo "$REPOS_DIR" "fx" "bad-subpath" "$SUB_SRC" "")"
ERR="$(echo "$R5" | jq -r '.error')"
if [ "$ERR" = "" ]; then
  failures+=("bad-subpath: expected a reported error")
fi
if [[ "$ERR" != *"no/such/dir"* ]]; then
  failures+=("bad-subpath: error must name the missing subdirectory")
fi

# empty/null source must still return one JSON object and exit 0
set +e
EMPTY_OUTPUT="$(sync_plugin_repo "$REPOS_DIR" "fx" "missing-source" "" "")"
EMPTY_STATUS=$?
set -e
ERR="$(echo "$EMPTY_OUTPUT" | jq -r '.error')"
if [ "$EMPTY_STATUS" -ne 0 ]; then
  failures+=("missing-source: expected exit 0, got '$EMPTY_STATUS'")
fi
if [ "$ERR" != "plugin 'missing-source' (from 'fx'): external source metadata is missing" ]; then
  failures+=("missing-source: expected structured metadata error, got '$ERR'")
fi

if [ ${#failures[@]} -gt 0 ]; then
  echo "FAIL: external plugin repo sync"
  printf '  - %s\n' "${failures[@]}"
  exit 1
fi

echo "PASS: sync_plugin_repo handles sha, ref, subpath, unpinned HEAD, idempotency, and reports unreachable commits"
