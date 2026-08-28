#!/usr/bin/env bash
# Bash mirror of verify-external.ps1: for every plugin declared across the
# external marketplaces, confirm its skills are actually linked into Codex,
# its plugin folder is actually linked into Antigravity, and every MCP
# server it declares actually appears in ~/.codex/config.toml. Exits
# non-zero and names every problem rather than passing on a partial sync.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CODEX_SKILLS_DIR="${CODEX_SKILLS_DIR:-$HOME/.agents/skills}"
ANTIGRAVITY_PLUGINS_DIR="${ANTIGRAVITY_PLUGINS_DIR:-$HOME/.gemini/config/plugins}"
CODEX_CONFIG_PATH="${CODEX_CONFIG_PATH:-$HOME/.codex/config.toml}"

# shellcheck source=/dev/null
source "$REPO_ROOT/bootstrap/sync.sh" --import

VENDOR_CACHE_DIR="$REPO_ROOT/.vendor-cache"
problems=()
expected_skills=0
expected_mcp=0
expected_mcp_servers=()

while IFS= read -r mp_json; do
  [ -n "$mp_json" ] || continue
  mp_name="$(echo "$mp_json" | jq -r '.name')"

  while IFS= read -r declared; do
    plugin_name="$(echo "$declared" | jq -r '.name')"
    [ -n "$plugin_name" ] || continue

    resolved_json="$(resolve_plugin_dir "$REPO_ROOT" "$VENDOR_CACHE_DIR" "$mp_json" "$declared")"
    failure="$(echo "$resolved_json" | jq -r '.failure // ""')"
    if [ -n "$failure" ]; then
      problems+=("$failure")
      continue
    fi
    plugin_dir="$(echo "$resolved_json" | jq -r '.dir')"

    skills_root="$plugin_dir/skills"
    if [ -d "$skills_root" ]; then
      for skill_dir in "$skills_root"/*/; do
        [ -d "$skill_dir" ] || continue
        skill_name="$(basename "$skill_dir")"
        expected_skills=$((expected_skills + 1))
        if [ ! -e "$CODEX_SKILLS_DIR/$skill_name" ]; then
          problems+=("Codex: skill '$skill_name' (from '$plugin_name') is not linked")
        fi
      done
    fi

    mcp_path="$plugin_dir/.mcp.json"
    if [ -f "$mcp_path" ]; then
      expected_mcp=$((expected_mcp + 1))
      if mcp_servers_json="$(jq -c '.mcpServers // {}' "$mcp_path" 2>/dev/null)"; then
        while IFS= read -r server_name; do
          [ -n "$server_name" ] || continue
          expected_mcp_servers+=("$server_name")
        done < <(echo "$mcp_servers_json" | jq -r 'keys[]')
      else
        problems+=("Plugin '$plugin_name' (from '$mp_name'): could not parse MCP config '$mcp_path'")
      fi
    fi

    if [ ! -e "$ANTIGRAVITY_PLUGINS_DIR/$plugin_name" ]; then
      problems+=("Antigravity: plugin '$plugin_name' is not linked")
    fi
  done < <(get_declared_plugins "$mp_json")
done < <(get_external_marketplaces_json "$REPO_ROOT")

actual_mcp=0
if [ -f "$CODEX_CONFIG_PATH" ]; then
  actual_mcp="$(grep -cE '^\[mcp_servers\.[^].]+\]$' "$CODEX_CONFIG_PATH" || true)"
fi

echo "Expected skills: $expected_skills   Expected plugins shipping MCP: $expected_mcp"
echo "Codex [mcp_servers.*] entries present: $actual_mcp"

if [ ${#problems[@]} -gt 0 ]; then
  echo ""
  echo "VERIFY FAILED (${#problems[@]} problems):"
  printf '  - %s\n' "${problems[@]}"
  exit 1
fi

if [ -f "$CODEX_CONFIG_PATH" ]; then
  if [ ${#expected_mcp_servers[@]} -gt 0 ]; then
    mapfile -t unique_servers < <(printf '%s\n' "${expected_mcp_servers[@]}" | sort -u)
    for server in "${unique_servers[@]}"; do
      if ! grep -qxF "[mcp_servers.$server]" "$CODEX_CONFIG_PATH"; then
        echo "VERIFY FAILED: Codex MCP server '$server' is missing from '$CODEX_CONFIG_PATH'."
        exit 1
      fi
    done
  fi
elif [ ${#expected_mcp_servers[@]} -gt 0 ]; then
  echo "VERIFY FAILED: Codex config '$CODEX_CONFIG_PATH' does not exist."
  exit 1
fi

if [ "$expected_skills" -eq 0 ]; then
  echo "VERIFY FAILED: resolved zero skills across the whole roster — resolution is not working."
  exit 1
fi

echo "VERIFY OK"
