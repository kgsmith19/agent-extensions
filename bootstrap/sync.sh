#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CODEX_SKILLS_DIR="${CODEX_SKILLS_DIR:-$HOME/.agents/skills}"
ANTIGRAVITY_PLUGINS_DIR="${ANTIGRAVITY_PLUGINS_DIR:-$HOME/.gemini/config/plugins}"
SKIP_CLAUDE_CODE="${SKIP_CLAUDE_CODE:-}"

get_plugin_names() {
  local repo_root="$1"
  if [ ! -d "$repo_root/plugins" ]; then
    echo "No plugins directory at '$repo_root/plugins'" >&2
    return 1
  fi
  find "$repo_root/plugins" -mindepth 1 -maxdepth 1 -type d -exec basename {} \;
}

new_or_repair_symlink() {
  local link_path="$1"
  local target_path="$2"

  if [ -e "$link_path" ] || [ -L "$link_path" ]; then
    if [ ! -L "$link_path" ]; then
      echo "Refusing to overwrite '$link_path' — it exists and is not a link this script manages." >&2
      return 1
    fi
    local current_target
    current_target="$(readlink "$link_path")"
    if [ "$current_target" = "$target_path" ]; then
      return 0  # already correct, idempotent no-op
    fi
    rm "$link_path"
  fi

  if ! ln -s "$target_path" "$link_path" || [ ! -L "$link_path" ]; then
    echo "Failed to create symlink '$link_path' -> '$target_path'" >&2
    return 1
  fi
}

get_external_marketplaces_json() {
  local repo_root="$1"
  local path="$repo_root/bootstrap/external-marketplaces.json"
  [ -f "$path" ] || return 0
  if ! command -v jq >/dev/null 2>&1; then
    echo "jq is required to read external-marketplaces.json but was not found on PATH." >&2
    return 1
  fi
  jq -c '.marketplaces[]?' "$path"
}

resolve_marketplace_url() {
  local repo="$1"
  if [[ "$repo" =~ ^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$ ]]; then
    echo "https://github.com/$repo.git"
  else
    echo "$repo"
  fi
}

sync_vendor_cache() {
  local repo_root="$1"
  local vendor_cache_dir="$2"

  if ! command -v jq >/dev/null 2>&1; then
    echo "jq is required for external-marketplace sync but was not found on PATH." >&2
    return 1
  fi

  local failed=0
  local mp_json name repo pinned_commit dest current_sha url
  while IFS= read -r mp_json; do
    [ -n "$mp_json" ] || continue
    name="$(echo "$mp_json" | jq -r '.name')"
    repo="$(echo "$mp_json" | jq -r '.repo')"
    pinned_commit="$(echo "$mp_json" | jq -r '.pinnedCommit')"
    dest="$vendor_cache_dir/$name"

    current_sha=""
    if [ -d "$dest/.git" ]; then
      current_sha="$(git -C "$dest" rev-parse HEAD 2>/dev/null || true)"
    fi
    if [ "$current_sha" = "$pinned_commit" ]; then
      continue
    fi

    if ! rm -rf "$dest"; then
      echo "Marketplace '$name': failed to clear existing vendor-cache directory '$dest'" >&2
      failed=1
      continue
    fi
    if ! mkdir -p "$dest"; then
      echo "Marketplace '$name': failed to create vendor-cache directory '$dest'" >&2
      failed=1
      continue
    fi
    url="$(resolve_marketplace_url "$repo")"

    if ! git -C "$dest" init -q \
        || ! git -C "$dest" remote add origin "$url" \
        || ! git -C "$dest" fetch --depth 1 origin "$pinned_commit" 2>/dev/null; then
      echo "Marketplace '$name': failed to fetch commit '$pinned_commit' from '$url' — it may no longer exist upstream." >&2
      failed=1
      continue
    fi
    if ! git -C "$dest" checkout -q FETCH_HEAD; then
      echo "Marketplace '$name': failed to check out pinned commit '$pinned_commit'." >&2
      failed=1
    fi
  done < <(get_external_marketplaces_json "$repo_root")

  return $failed
}

sync_codex_skills() {
  local repo_root="$1"
  local codex_skills_dir="$2"
  mkdir -p "$codex_skills_dir"

  local plugin_names
  mapfile -t plugin_names < <(get_plugin_names "$repo_root") || return 1
  if [ ${#plugin_names[@]} -eq 0 ]; then
    echo "No plugins found under '$repo_root/plugins'" >&2
    return 1
  fi

  local plugin skills_root
  for plugin in "${plugin_names[@]}"; do
    skills_root="$repo_root/plugins/$plugin/skills"
    [ -d "$skills_root" ] || continue
    local skill_dir skill_name
    for skill_dir in "$skills_root"/*/; do
      [ -d "$skill_dir" ] || continue
      skill_name="$(basename "$skill_dir")"
      new_or_repair_symlink "$codex_skills_dir/$skill_name" "${skill_dir%/}"
    done
  done
}

sync_antigravity_plugins() {
  local repo_root="$1"
  local antigravity_plugins_dir="$2"
  mkdir -p "$antigravity_plugins_dir"

  local plugin_names
  mapfile -t plugin_names < <(get_plugin_names "$repo_root") || return 1
  if [ ${#plugin_names[@]} -eq 0 ]; then
    echo "No plugins found under '$repo_root/plugins'" >&2
    return 1
  fi

  local plugin
  for plugin in "${plugin_names[@]}"; do
    new_or_repair_symlink "$antigravity_plugins_dir/$plugin" "$repo_root/plugins/$plugin"
  done
}

sync_claude_code_marketplace() {
  local repo_root="$1"

  if ! claude plugin marketplace add "$repo_root"; then
    echo "claude plugin marketplace add '$repo_root' failed" >&2
    return 1
  fi

  local plugin_names
  mapfile -t plugin_names < <(get_plugin_names "$repo_root") || return 1
  if [ ${#plugin_names[@]} -eq 0 ]; then
    echo "No plugins found under '$repo_root/plugins'" >&2
    return 1
  fi

  local plugin
  for plugin in "${plugin_names[@]}"; do
    if ! claude plugin install "$plugin@agent-extensions"; then
      echo "claude plugin install '$plugin@agent-extensions' failed" >&2
      return 1
    fi
  done
}

if [ "${1:-}" = "--import" ]; then
  return 0 2>/dev/null || exit 0
fi

sync_codex_skills "$REPO_ROOT" "$CODEX_SKILLS_DIR"
sync_antigravity_plugins "$REPO_ROOT" "$ANTIGRAVITY_PLUGINS_DIR"
if [ -z "$SKIP_CLAUDE_CODE" ]; then
  sync_claude_code_marketplace "$REPO_ROOT"
fi

echo "Sync complete."
