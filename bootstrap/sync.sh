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

get_declared_plugins() {
  local mp_json="$1"
  printf '%s\n' "$mp_json" | jq -c '.plugins[]? | if type == "string" then {name: ., resolvedCommit: ""} else {name: .name, resolvedCommit: (.resolvedCommit // "")} end' 2>/dev/null || true
}

plugin_source_missing() {
  jq -n --arg e "$1" '{kind:"missing",path:"",url:"",sha:"",ref:"",subpath:"",error:$e}'
}

get_plugin_source() {
  local marketplace_dir="$1" plugin_name="$2"
  local manifest="$marketplace_dir/.claude-plugin/marketplace.json"

  if [ ! -f "$manifest" ]; then
    plugin_source_missing "no .claude-plugin/marketplace.json found at '$marketplace_dir'"
    return 0
  fi
  if ! jq -e . "$manifest" >/dev/null 2>&1; then
    plugin_source_missing "could not parse '$manifest'"
    return 0
  fi

  jq -c --arg n "$plugin_name" --arg m "$manifest" '
    [.plugins[]? | select(.name == $n)] as $matches
    | if ($matches | length) == 0 then
        {kind:"missing",path:"",url:"",sha:"",ref:"",subpath:"",
         error:("plugin \u0027" + $n + "\u0027 is not declared in \u0027" + $m + "\u0027")}
      else ($matches[0]) as $e
      | if ($e.source | type) == "string" then
          {kind:"inline",path:$e.source,url:"",sha:"",ref:"",subpath:"",error:""}
        elif $e.source.source == "url" then
          {kind:"repo",path:"",url:($e.source.url // ""),sha:($e.source.sha // ""),
           ref:($e.source.ref // ""),subpath:($e.source.path // ""),error:""}
        else
          {kind:"missing",path:"",url:"",sha:"",ref:"",subpath:"",
           error:("plugin \u0027" + $n + "\u0027 declares unsupported source kind")}
        end
      end' "$manifest"
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

plugin_repo_result() {
  jq -n --arg d "$1" --arg s "$2" --arg e "$3" '{dir:$d,resolvedSha:$s,error:$e}'
}

sync_plugin_repo() {
  local plugin_repos_dir="$1" marketplace_name="$2" plugin_name="$3" source_json="$4" pinned_commit="$5"
  local url sha ref subpath kind source_error wanted wanted_commit wanted_ref clone current fetch_target resolved dir

  if [ "$source_json" = "null" ] || [ -z "$source_json" ]; then
    plugin_repo_result "" "" "plugin '$plugin_name' (from '$marketplace_name'): external source metadata is missing"
    return 0
  fi
  url="$(echo "$source_json" | jq -r '.url // ""')"
  sha="$(echo "$source_json" | jq -r '.sha // ""')"
  ref="$(echo "$source_json" | jq -r '.ref // ""')"
  subpath="$(echo "$source_json" | jq -r '.subpath // ""')"
  source_error="$(echo "$source_json" | jq -r '.error // ""')"
  kind="$(echo "$source_json" | jq -r '.kind // ""')"

  if [ "$source_error" != "" ]; then
    plugin_repo_result "" "" "plugin '$plugin_name' (from '$marketplace_name'): $source_error"
    return 0
  fi
  if [ "$kind" != "repo" ]; then
    plugin_repo_result "" "" "plugin '$plugin_name' (from '$marketplace_name'): source kind '$kind' is not an external repo"
    return 0
  fi
  if [ -z "$url" ]; then
    plugin_repo_result "" "" "plugin '$plugin_name' (from '$marketplace_name'): external source declares no url"
    return 0
  fi

  wanted=""
  wanted_commit=""
  wanted_ref=""
  if [ -n "$pinned_commit" ]; then
    wanted="$pinned_commit"
    wanted_commit="$pinned_commit"
  elif [ -n "$sha" ]; then
    wanted="$sha"
    wanted_commit="$sha"
  elif [ -n "$ref" ]; then
    wanted="$ref"
    wanted_ref="$ref"
  fi

  clone="$plugin_repos_dir/$marketplace_name/$plugin_name"
  current=""
  if [ -d "$clone/.git" ]; then
    current="$(cd "$clone" && git rev-parse HEAD 2>/dev/null || true)"
    if [ -n "$wanted_ref" ]; then
      wanted_commit="$(cd "$clone" && git ls-remote --refs --tags --heads origin "$wanted_ref" 2>/dev/null | awk 'NR==1 {print $1}')"
    fi
  fi

  if [ -z "$current" ] || [ -z "$wanted_commit" ] || [ "$current" != "$wanted_commit" ]; then
    rm -rf "$clone"
    if ! mkdir -p "$clone"; then
      plugin_repo_result "" "" "plugin '$plugin_name' (from '$marketplace_name'): could not create '$clone'"
      return 0
    fi

    fetch_target="$wanted"
    if [ -z "$fetch_target" ]; then
      fetch_target="HEAD"
    fi

    if ! (
      cd "$clone" \
      && git init -q \
      && git remote add origin "$url" \
      && git fetch --depth 1 origin "$fetch_target" >/dev/null 2>&1 \
      && git checkout -q FETCH_HEAD
    ); then
      plugin_repo_result "" "" "plugin '$plugin_name' (from '$marketplace_name'): could not fetch '$fetch_target' from '$url'"
      return 0
    fi
  fi

  resolved="$(cd "$clone" && git rev-parse HEAD 2>/dev/null || true)"
  if [ -z "$resolved" ]; then
    plugin_repo_result "" "" "plugin '$plugin_name' (from '$marketplace_name'): clone at '$clone' has no resolvable HEAD"
    return 0
  fi

  dir="$clone"
  if [ -n "$subpath" ]; then
    dir="$clone/$subpath"
    if [ ! -d "$dir" ]; then
      plugin_repo_result "" "" "plugin '$plugin_name' (from '$marketplace_name'): declared subdirectory '$subpath' does not exist in the clone"
      return 0
    fi
  fi

  plugin_repo_result "$dir" "$resolved" ""
}

save_resolved_commit() {
  local repo_root="$1" marketplace_name="$2" plugin_name="$3" sha="$4"
  local path="$repo_root/bootstrap/external-marketplaces.json"
  local tmp

  if [ ! -f "$path" ]; then
    echo "cannot record resolved commit: '$path' does not exist" >&2
    return 1
  fi

  if ! jq -e . "$path" >/dev/null 2>&1; then
    echo "cannot record resolved commit: could not parse '$path'" >&2
    return 1
  fi

  if ! jq -e --arg m "$marketplace_name" '.marketplaces[] | select(.name == $m)' "$path" >/dev/null 2>&1; then
    echo "cannot record resolved commit: marketplace '$marketplace_name' is not declared in '$path'" >&2
    return 1
  fi

  if ! jq -e --arg m "$marketplace_name" --arg p "$plugin_name" \
    '.marketplaces[] | select(.name == $m) | .plugins[] | select((if type == "string" then . else .name end) == $p)' \
    "$path" >/dev/null 2>&1; then
    echo "cannot record resolved commit: plugin '$plugin_name' is not declared under marketplace '$marketplace_name'" >&2
    return 1
  fi

  tmp="$(mktemp)"
  if ! jq --arg m "$marketplace_name" --arg p "$plugin_name" --arg s "$sha" '
    .marketplaces |= map(
      if .name == $m then
        .plugins |= map(
          if (if type == "string" then . else .name end) == $p
          then {name: $p, resolvedCommit: $s}
          else .
          end
        )
      else .
      end
    )' "$path" > "$tmp"; then
    rm -f "$tmp"
    echo "cannot record resolved commit: failed to rewrite '$path'" >&2
    return 1
  fi

  if ! mv "$tmp" "$path"; then
    rm -f "$tmp"
    echo "cannot record resolved commit: failed to rewrite '$path'" >&2
    return 1
  fi
}

mcp_json_to_codex_toml() {
  local mcp_servers_json="$1"

  local names name server has_command has_url allowed_regex unknown out=""
  names="$(echo "$mcp_servers_json" | jq -r 'keys[]')"

  while IFS= read -r name; do
    [ -n "$name" ] || continue
    server="$(echo "$mcp_servers_json" | jq -c --arg n "$name" '.[$n]')"
    has_command="$(echo "$server" | jq 'has("command")')"
    has_url="$(echo "$server" | jq 'has("url")')"

    if [ "$has_command" = "false" ] && [ "$has_url" = "false" ]; then
      echo "MCP server '$name' has neither 'command' (stdio) nor 'url' (http) — unrecognized server shape." >&2
      return 1
    fi
    if [ "$has_command" = "true" ] && [ "$has_url" = "true" ]; then
      echo "MCP server '$name' has both 'command' and 'url' — ambiguous transport, cannot translate." >&2
      return 1
    fi

    if [ "$has_command" = "true" ]; then
      allowed_regex='^(command|args|env)$'
    else
      allowed_regex='^(url|headers)$'
    fi
    unknown="$(echo "$server" | jq -r --arg re "$allowed_regex" 'keys[] | select(test($re) | not)')"
    if [ -n "$unknown" ]; then
      echo "MCP server '$name' has unrecognized field(s): $(echo "$unknown" | tr '\n' ' ')." >&2
      return 1
    fi

    out+="[mcp_servers.$name]"$'\n'
    if [ "$has_command" = "true" ]; then
      out+="command = $(echo "$server" | jq '.command')"$'\n'
      if echo "$server" | jq -e 'has("args")' >/dev/null; then
        out+="args = $(echo "$server" | jq -c '.args')"$'\n'
      fi
      if echo "$server" | jq -e 'has("env")' >/dev/null; then
        local env_pairs
        env_pairs="$(echo "$server" | jq -r '.env | to_entries | map("\(.key) = \(.value | tojson)") | join(", ")')"
        out+="env = { $env_pairs }"$'\n'
      fi
    else
      out+="url = $(echo "$server" | jq '.url')"$'\n'
      if echo "$server" | jq -e 'has("headers")' >/dev/null; then
        local header_pairs
        header_pairs="$(echo "$server" | jq -r '.headers | to_entries | map("\(.key) = \(.value | tojson)") | join(", ")')"
        out+="http_headers = { $header_pairs }"$'\n'
      fi
    fi
    out+=$'\n'
  done <<< "$names"

  printf '%s' "$out"
}

mcp_json_to_antigravity_config() {
  local mcp_servers_json="$1"

  local names name server has_command has_url allowed_regex unknown
  names="$(echo "$mcp_servers_json" | jq -r 'keys[]')"

  while IFS= read -r name; do
    [ -n "$name" ] || continue
    server="$(echo "$mcp_servers_json" | jq -c --arg n "$name" '.[$n]')"
    has_command="$(echo "$server" | jq 'has("command")')"
    has_url="$(echo "$server" | jq 'has("url")')"

    if [ "$has_command" = "false" ] && [ "$has_url" = "false" ]; then
      echo "MCP server '$name' has neither 'command' (stdio) nor 'url' (http) — unrecognized server shape." >&2
      return 1
    fi
    if [ "$has_command" = "true" ] && [ "$has_url" = "true" ]; then
      echo "MCP server '$name' has both 'command' and 'url' — ambiguous transport, cannot translate." >&2
      return 1
    fi
    if [ "$has_command" = "true" ]; then
      allowed_regex='^(command|args|env)$'
    else
      allowed_regex='^(url|headers)$'
    fi
    unknown="$(echo "$server" | jq -r --arg re "$allowed_regex" 'keys[] | select(test($re) | not)')"
    if [ -n "$unknown" ]; then
      echo "MCP server '$name' has unrecognized field(s): $(echo "$unknown" | tr '\n' ' ')." >&2
      return 1
    fi
  done <<< "$names"

  jq -n --argjson servers "$mcp_servers_json" '{mcpServers: $servers}'
}

merge_codex_mcp_config() {
  local config_path="$1"
  shift
  local begin_marker="# >>> agent-extensions managed mcp_servers (do not edit within this block) >>>"
  local end_marker="# <<< agent-extensions managed mcp_servers <<<"

  local existing=""
  [ -f "$config_path" ] && existing="$(cat "$config_path")"

  local before
  before="$(awk -v b="$begin_marker" -v e="$end_marker" '
    $0==b {skip=1; next}
    $0==e {skip=0; next}
    skip {next}
    {print}
  ' <<< "$existing")"

  local block="$begin_marker"$'\n'
  while [ $# -ge 2 ]; do
    local plugin="$1" toml="$2"
    shift 2
    [ -n "$toml" ] || continue
    block+="# plugin: $plugin"$'\n'
    block+="$toml"$'\n'
  done
  block+="$end_marker"

  mkdir -p "$(dirname "$config_path")"
  {
    if [ -n "$(printf '%s' "$before" | tr -d '[:space:]')" ]; then
      printf '%s\n\n' "$before"
    fi
    printf '%s\n' "$block"
  } > "$config_path"
}

write_antigravity_mcp_config() {
  local plugin_staged_dir="$1"
  local json_content="$2"
  [ -n "$json_content" ] || return 0
  mkdir -p "$plugin_staged_dir"
  printf '%s' "$json_content" > "$plugin_staged_dir/mcp_config.json"
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

sync_external_codex_content() {
  local repo_root="$1"
  local vendor_cache_dir="$2"
  local codex_skills_dir="$3"
  local codex_config_path="$4"

  local failed=0
  local merge_args=()
  local mp_json name plugin_line plugin plugin_dir skills_root mcp_path mcp_servers toml

  while IFS= read -r mp_json; do
    [ -n "$mp_json" ] || continue
    name="$(echo "$mp_json" | jq -r '.name')"

    while IFS= read -r plugin_line; do
      plugin="$(echo "$plugin_line" | jq -r '.')"
      [ -n "$plugin" ] || continue
      plugin_dir="$vendor_cache_dir/$name/$plugin"

      skills_root="$plugin_dir/skills"
      if [ -d "$skills_root" ]; then
        local skill_dir skill_name
        for skill_dir in "$skills_root"/*/; do
          [ -d "$skill_dir" ] || continue
          skill_name="$(basename "$skill_dir")"
          if ! new_or_repair_symlink "$codex_skills_dir/$skill_name" "${skill_dir%/}"; then
            echo "Plugin '$plugin' (from '$name'): failed to link skill '$skill_name'" >&2
            failed=1
          fi
        done
      fi

      mcp_path="$plugin_dir/.mcp.json"
      if [ -f "$mcp_path" ]; then
        if ! mcp_servers="$(jq -c '.mcpServers // {}' "$mcp_path")"; then
          echo "Plugin '$plugin' (from '$name'): failed to parse '$mcp_path' as JSON" >&2
          failed=1
        elif toml="$(mcp_json_to_codex_toml "$mcp_servers")"; then
          if [ -n "$toml" ]; then
            merge_args+=("$plugin" "$toml")
          fi
        else
          echo "Plugin '$plugin' (from '$name'): MCP translation to Codex TOML failed" >&2
          failed=1
        fi
      fi
    done < <(echo "$mp_json" | jq -c '.plugins[]')
  done < <(get_external_marketplaces_json "$repo_root")

  if [ ${#merge_args[@]} -gt 0 ]; then
    if ! merge_codex_mcp_config "$codex_config_path" "${merge_args[@]}"; then
      echo "Failed to merge translated MCP servers into '$codex_config_path'" >&2
      failed=1
    fi
  fi

  return $failed
}

sync_external_antigravity_content() {
  local repo_root="$1"
  local vendor_cache_dir="$2"
  local staged_dir="$3"
  local antigravity_plugins_dir="$4"

  local failed=0
  local mp_json name plugin_line plugin plugin_dir staged_plugin_dir
  local skills_source mcp_path mcp_servers config final_link

  while IFS= read -r mp_json; do
    [ -n "$mp_json" ] || continue
    name="$(echo "$mp_json" | jq -r '.name')"

    while IFS= read -r plugin_line; do
      plugin="$(echo "$plugin_line" | jq -r '.')"
      [ -n "$plugin" ] || continue
      plugin_dir="$vendor_cache_dir/$name/$plugin"
      staged_plugin_dir="$staged_dir/antigravity/$plugin"
      mkdir -p "$staged_plugin_dir"

      skills_source="$plugin_dir/skills"
      if [ -d "$skills_source" ]; then
        if ! new_or_repair_symlink "$staged_plugin_dir/skills" "$skills_source"; then
          echo "Plugin '$plugin' (from '$name'): failed to link skills into staging" >&2
          failed=1
          continue
        fi
      fi

      mcp_path="$plugin_dir/.mcp.json"
      if [ -f "$mcp_path" ]; then
        if ! mcp_servers="$(jq -c '.mcpServers // {}' "$mcp_path")"; then
          echo "Plugin '$plugin' (from '$name'): failed to parse '$mcp_path' as JSON" >&2
          failed=1
          continue
        elif config="$(mcp_json_to_antigravity_config "$mcp_servers")"; then
          write_antigravity_mcp_config "$staged_plugin_dir" "$config"
        else
          echo "Plugin '$plugin' (from '$name'): MCP translation to Antigravity config failed" >&2
          failed=1
          continue
        fi
      fi

      final_link="$antigravity_plugins_dir/$plugin"
      if ! new_or_repair_symlink "$final_link" "$staged_plugin_dir"; then
        echo "Plugin '$plugin' (from '$name'): failed to link into Antigravity plugins dir" >&2
        failed=1
      fi
    done < <(echo "$mp_json" | jq -c '.plugins[]')
  done < <(get_external_marketplaces_json "$repo_root")

  return $failed
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

  local failed=0
  local mp_json name repo plugin_line pname
  while IFS= read -r mp_json; do
    [ -n "$mp_json" ] || continue
    name="$(echo "$mp_json" | jq -r '.name')"
    repo="$(echo "$mp_json" | jq -r '.repo')"

    if ! claude plugin marketplace add "$repo"; then
      echo "claude plugin marketplace add '$repo' failed" >&2
      failed=1
      continue
    fi

    while IFS= read -r plugin_line; do
      pname="$(echo "$plugin_line" | jq -r '.')"
      [ -n "$pname" ] || continue
      if ! claude plugin install "$pname@$name"; then
        echo "claude plugin install '$pname@$name' failed" >&2
        failed=1
      fi
    done < <(echo "$mp_json" | jq -c '.plugins[]')
  done < <(get_external_marketplaces_json "$repo_root")

  return $failed
}

if [ "${1:-}" = "--import" ]; then
  return 0 2>/dev/null || exit 0
fi

VENDOR_CACHE_DIR="$REPO_ROOT/.vendor-cache"
STAGED_DIR="$VENDOR_CACHE_DIR/_staged"
CODEX_CONFIG_PATH="$HOME/.codex/config.toml"

overall_failed=0

sync_codex_skills "$REPO_ROOT" "$CODEX_SKILLS_DIR" || overall_failed=1
sync_antigravity_plugins "$REPO_ROOT" "$ANTIGRAVITY_PLUGINS_DIR" || overall_failed=1

sync_vendor_cache "$REPO_ROOT" "$VENDOR_CACHE_DIR" || overall_failed=1
sync_external_codex_content "$REPO_ROOT" "$VENDOR_CACHE_DIR" "$CODEX_SKILLS_DIR" "$CODEX_CONFIG_PATH" || overall_failed=1
sync_external_antigravity_content "$REPO_ROOT" "$VENDOR_CACHE_DIR" "$STAGED_DIR" "$ANTIGRAVITY_PLUGINS_DIR" || overall_failed=1

if [ -z "$SKIP_CLAUDE_CODE" ]; then
  sync_claude_code_marketplace "$REPO_ROOT" || overall_failed=1
fi

if [ "$overall_failed" -ne 0 ]; then
  echo "Sync completed with failures (see messages above)." >&2
  exit 1
fi

echo "Sync complete."
