#!/usr/bin/env bash
# One command: declared roster against actual state on every provider and
# surface. Runs every check, aggregates failures, and exits non-zero naming
# every difference — never stopping at the first failure, so one broken
# surface doesn't hide problems on another.
set -uo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=/dev/null
source "$REPO_ROOT/bootstrap/sync.sh" --import
# sync.sh sets -e for itself; sourcing runs it in this same shell, so
# without this, -e would leak in here too and defeat the whole point of
# running every check regardless of individual failures.
set +e
set -uo pipefail

overall_failed=0
checks_ok=()
checks_failed=()

run_check() {
  local check_name="$1"
  shift
  echo "--- $check_name ---"
  if "$@"; then
    checks_ok+=("$check_name")
  else
    checks_failed+=("$check_name")
    overall_failed=1
  fi
  echo
}

check_content() {
  bash "$REPO_ROOT/bootstrap/verify-content.sh" "$REPO_ROOT"
}

check_pointers() {
  bash "$REPO_ROOT/bootstrap/verify-pointers.sh" "$REPO_ROOT"
}

check_external() {
  bash "$REPO_ROOT/bootstrap/verify-external.sh"
}

check_account() {
  local manifest_path="$REPO_ROOT/bootstrap/account-manifest.json"
  local failed=0 name source skill_json

  if [ ! -f "$manifest_path" ]; then
    echo "Missing $manifest_path"
    return 1
  fi
  if ! jq -e . "$manifest_path" >/dev/null 2>&1; then
    echo "$manifest_path is not valid JSON"
    return 1
  fi

  while IFS= read -r skill_json; do
    [ -n "$skill_json" ] || continue
    name="$(echo "$skill_json" | jq -r '.name')"
    source="$(echo "$skill_json" | jq -r '.source')"
    if [ ! -d "$REPO_ROOT/$source" ]; then
      echo "Account skill '$name': declared source '$source' does not exist"
      failed=1
    fi
  done < <(jq -c '.skills[]?' "$manifest_path")

  # A pending ADD/REMOVE checklist is expected, routine state (Kyle hasn't
  # uploaded the latest change by hand yet) — reported for visibility, but
  # not itself a verification failure the way a broken source path is.
  print_account_checklist "$REPO_ROOT"
  return $failed
}

check_translations() {
  # Regenerating and diffing against the committed file is the freshness
  # check: if the roster changed since the report was last committed,
  # regenerating it now produces a real diff. Requires .vendor-cache to be
  # populated by a prior sync — same assumption verify-external.sh makes.
  local before after failed=0
  before="$(cat "$REPO_ROOT/bootstrap/command-gap-report.md" 2>/dev/null || true)"
  generate_command_gap_report "$REPO_ROOT" "$REPO_ROOT/.vendor-cache"
  after="$(cat "$REPO_ROOT/bootstrap/command-gap-report.md" 2>/dev/null || true)"
  if [ "$before" != "$after" ]; then
    echo "bootstrap/command-gap-report.md was stale relative to the declared roster — regenerated, review and commit the diff"
    failed=1
  else
    echo "command-gap-report.md matches the declared roster"
  fi

  # Every mechanically-translatable agent should have produced a file on
  # each target — this is the check that catches one being deleted or
  # never generated out-of-band, which a clean sync exit code alone would
  # not reveal until the next sync silently regenerated it.
  local mp_json plugin declared resolved_json plugin_dir agents_root
  local agent_file base parsed expected=0
  while IFS= read -r mp_json; do
    [ -n "$mp_json" ] || continue
    while IFS= read -r declared; do
      plugin="$(echo "$declared" | jq -r '.name')"
      [ -n "$plugin" ] || continue
      resolved_json="$(resolve_plugin_dir "$REPO_ROOT" "$REPO_ROOT/.vendor-cache" "$mp_json" "$declared")"
      [ -z "$(echo "$resolved_json" | jq -r '.failure')" ] || continue
      plugin_dir="$(echo "$resolved_json" | jq -r '.dir')"
      agents_root="$plugin_dir/agents"
      [ -d "$agents_root" ] || continue
      for agent_file in "$agents_root"/*.md; do
        [ -f "$agent_file" ] || continue
        parsed="$(parse_agent_frontmatter "$agent_file")"
        [ -z "$(echo "$parsed" | jq -r '.error')" ] || continue
        expected=$((expected + 1))
        base="$plugin-$(basename "$agent_file" .md)"
        if [ ! -f "$CODEX_AGENTS_DIR/$base.toml" ]; then
          echo "Codex agent '$base' is missing (source translates cleanly but no file was found)"
          failed=1
        fi
        if [ ! -f "$ANTIGRAVITY_AGENTS_DIR/$base.md" ]; then
          echo "Antigravity agent '$base' is missing (source translates cleanly but no file was found)"
          failed=1
        fi
      done
    done < <(get_declared_plugins "$mp_json")
  done < <(get_external_marketplaces_json "$REPO_ROOT")
  echo "Checked $expected mechanically-translatable agent(s) against both targets"

  return $failed
}

run_check "content" check_content
run_check "pointers" check_pointers
run_check "external" check_external
run_check "account" check_account
run_check "translations" check_translations

echo "--- verify summary ---"
if [ ${#checks_ok[@]} -gt 0 ]; then
  echo "OK: ${checks_ok[*]}"
fi
if [ ${#checks_failed[@]} -gt 0 ]; then
  echo "FAILED: ${checks_failed[*]}"
fi

if [ "$overall_failed" -ne 0 ]; then
  echo "VERIFY FAILED"
  exit 1
fi

echo "VERIFY OK"
exit 0
