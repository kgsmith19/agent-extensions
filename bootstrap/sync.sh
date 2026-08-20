#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CODEX_SKILLS_DIR="${CODEX_SKILLS_DIR:-$HOME/.agents/skills}"
ANTIGRAVITY_PLUGINS_DIR="${ANTIGRAVITY_PLUGINS_DIR:-$HOME/.gemini/config/plugins}"
SKIP_CLAUDE_CODE="${SKIP_CLAUDE_CODE:-}"

get_plugin_names() {
  local repo_root="$1"
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

  if ! ln -s "$target_path" "$link_path"; then
    echo "Failed to create symlink '$link_path' -> '$target_path'" >&2
    return 1
  fi
}

sync_codex_skills() {
  local repo_root="$1"
  local codex_skills_dir="$2"
  mkdir -p "$codex_skills_dir"

  local plugin skills_root
  while IFS= read -r plugin; do
    skills_root="$repo_root/plugins/$plugin/skills"
    [ -d "$skills_root" ] || continue
    local skill_dir skill_name
    for skill_dir in "$skills_root"/*/; do
      [ -d "$skill_dir" ] || continue
      skill_name="$(basename "$skill_dir")"
      new_or_repair_symlink "$codex_skills_dir/$skill_name" "${skill_dir%/}"
    done
  done < <(get_plugin_names "$repo_root")
}

sync_antigravity_plugins() {
  local repo_root="$1"
  local antigravity_plugins_dir="$2"
  mkdir -p "$antigravity_plugins_dir"

  local plugin
  while IFS= read -r plugin; do
    new_or_repair_symlink "$antigravity_plugins_dir/$plugin" "$repo_root/plugins/$plugin"
  done < <(get_plugin_names "$repo_root")
}

sync_claude_code_marketplace() {
  local repo_root="$1"

  claude plugin marketplace add "$repo_root"

  local plugin
  while IFS= read -r plugin; do
    claude plugin install "$plugin@agent-extensions"
  done < <(get_plugin_names "$repo_root")
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
