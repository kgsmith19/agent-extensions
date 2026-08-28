#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CODEX_SKILLS_DIR_OVERRIDDEN="${CODEX_SKILLS_DIR:+1}"
CODEX_SKILLS_DIR="${CODEX_SKILLS_DIR:-$HOME/.agents/skills}"
CODEX_AGENTS_DIR="${CODEX_AGENTS_DIR:-$HOME/.codex/agents}"
ANTIGRAVITY_PLUGINS_DIR_OVERRIDDEN="${ANTIGRAVITY_PLUGINS_DIR:+1}"
ANTIGRAVITY_PLUGINS_DIR="${ANTIGRAVITY_PLUGINS_DIR:-$HOME/.gemini/config/plugins}"
ANTIGRAVITY_AGENTS_DIR="${ANTIGRAVITY_AGENTS_DIR:-$HOME/.gemini/config/agents}"
SKIP_CLAUDE_CODE="${SKIP_CLAUDE_CODE:-}"
HOOK_ENV_WRAPPER="$REPO_ROOT/bootstrap/hook_env_wrapper.py"

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
        elif ($e.source.source == "url" or $e.source.source == "git-subdir") then
          {kind:"repo",path:"",url:($e.source.url // ""),sha:($e.source.sha // ""),
           ref:($e.source.ref // ""),subpath:($e.source.path // ""),error:""}
        else
          {kind:"missing",path:"",url:"",sha:"",ref:"",subpath:"",
           error:("plugin \u0027" + $n + "\u0027 declares unsupported source kind")}
        end
      end' "$manifest"
}

# --- Agent translation (Claude markdown -> Codex TOML / Antigravity MD) ---
#
# Real plugin data includes YAML frontmatter this codebase has no library
# to parse safely: multi-line block scalars (`description: |`), quoted
# values with embedded punctuation, files with no `name:` at all. Rather
# than hand-roll a YAML parser and risk silently mangling those (the exact
# failure mode Spec 1's amendment had to fix elsewhere), this only
# translates single-line scalar values it can extract with confidence and
# reports anything else as a declared per-agent failure — never a guess.
#
# Deliberately NOT translated: `tools` and `model`. Claude's tool names
# (Read, Grep, Bash, ...) and model aliases (sonnet, opus) have no
# principled mapping to Codex's or Antigravity's own tool/model
# vocabularies — carrying them over as literal strings would silently
# produce wrong (not just incomplete) configuration. Translated agents
# get the target provider's default tools and default model instead.

extract_yaml_scalar() {
  local raw
  raw="$(printf '%s' "$1" | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')"
  case "$raw" in
    ''|'|'|'|-'|'|+'|'>'|'>-'|'>+')
      echo "__COMPLEX__"
      return 0
      ;;
  esac
  if [[ "$raw" == \'*\' && "${#raw}" -ge 2 ]]; then
    raw="${raw#\'}"; raw="${raw%\'}"; raw="${raw//\'\'/\'}"
    printf '%s' "$raw"
    return 0
  fi
  if [[ "$raw" == \"*\" && "${#raw}" -ge 2 ]]; then
    raw="${raw#\"}"; raw="${raw%\"}"
    printf '%s' "$raw"
    return 0
  fi
  printf '%s' "$raw"
}

parse_agent_frontmatter() {
  local agent_md_path="$1"
  local frontmatter name_line desc_line name description

  if [ "$(sed -n '1p' "$agent_md_path")" != "---" ]; then
    jq -n --arg e "no YAML frontmatter (file does not start with '---')" '{name:"",description:"",error:$e}'
    return 0
  fi

  frontmatter="$(awk 'NR==1{next} /^---[[:space:]]*$/{exit} {print}' "$agent_md_path")"
  name_line="$(printf '%s\n' "$frontmatter" | grep -m1 '^name:')"
  desc_line="$(printf '%s\n' "$frontmatter" | grep -m1 '^description:')"

  if [ -z "$name_line" ]; then
    name="$(basename "$agent_md_path" .md)"
  else
    name="$(extract_yaml_scalar "${name_line#name:}")"
    if [ "$name" = "__COMPLEX__" ]; then
      jq -n --arg e "'name' is not a simple single-line value" '{name:"",description:"",error:$e}'
      return 0
    fi
  fi

  if [ -z "$desc_line" ]; then
    description=""
  else
    description="$(extract_yaml_scalar "${desc_line#description:}")"
    if [ "$description" = "__COMPLEX__" ]; then
      jq -n --arg e "'description' is a multi-line/block YAML value — not mechanically translatable" '{name:"",description:"",error:$e}'
      return 0
    fi
  fi

  jq -n --arg n "$name" --arg d "$description" '{name:$n,description:$d,error:""}'
}

get_agent_body() {
  awk 'BEGIN{c=0} /^---[[:space:]]*$/{c++; next} c>=2{print}' "$1"
}

translate_agent_to_codex_toml() {
  local agent_md_path="$1" plugin_name="$2"
  local parsed error name description body qualified_name

  parsed="$(parse_agent_frontmatter "$agent_md_path")"
  error="$(echo "$parsed" | jq -r '.error')"
  if [ -n "$error" ]; then
    echo "Agent '$(basename "$agent_md_path")' (from '$plugin_name'): $error" >&2
    return 1
  fi
  name="$(echo "$parsed" | jq -r '.name')"
  description="$(echo "$parsed" | jq -r '.description')"
  qualified_name="$plugin_name-$name"
  body="$(get_agent_body "$agent_md_path")"

  if [[ "$body" == *"'''"* ]]; then
    echo "Agent '$qualified_name': system prompt contains a literal ''' sequence, which TOML literal strings cannot safely embed" >&2
    return 1
  fi

  printf 'name = %s\n' "$(jq -rn --arg v "$qualified_name" '$v|tojson')"
  printf 'description = %s\n' "$(jq -rn --arg v "$description" '$v|tojson')"
  printf "developer_instructions = '''\n%s\n'''\n" "$body"
}

translate_agent_to_antigravity_md() {
  local agent_md_path="$1" plugin_name="$2"
  local parsed error name description body qualified_name

  parsed="$(parse_agent_frontmatter "$agent_md_path")"
  error="$(echo "$parsed" | jq -r '.error')"
  if [ -n "$error" ]; then
    echo "Agent '$(basename "$agent_md_path")' (from '$plugin_name'): $error" >&2
    return 1
  fi
  name="$(echo "$parsed" | jq -r '.name')"
  description="$(echo "$parsed" | jq -r '.description')"
  qualified_name="$plugin_name-$name"
  body="$(get_agent_body "$agent_md_path")"

  printf -- '---\n'
  printf 'name: %s\n' "$(jq -rn --arg v "$qualified_name" '$v|tojson')"
  printf 'description: %s\n' "$(jq -rn --arg v "$description" '$v|tojson')"
  printf -- '---\n\n%s\n' "$body"
}

sync_plugin_agents_codex() {
  local plugin_dir="$1" plugin_name="$2" codex_agents_dir="$3"
  local agents_root="$plugin_dir/agents"
  [ -d "$agents_root" ] || return 0

  local failed=0 agent_file base toml
  for agent_file in "$agents_root"/*.md; do
    [ -f "$agent_file" ] || continue
    base="$(basename "$agent_file" .md)"
    if toml="$(translate_agent_to_codex_toml "$agent_file" "$plugin_name")"; then
      mkdir -p "$codex_agents_dir"
      printf '%s' "$toml" > "$codex_agents_dir/$plugin_name-$base.toml"
    else
      failed=1
    fi
  done
  return $failed
}

sync_plugin_agents_antigravity() {
  local plugin_dir="$1" plugin_name="$2" antigravity_agents_dir="$3"
  local agents_root="$plugin_dir/agents"
  [ -d "$agents_root" ] || return 0

  local failed=0 agent_file base md
  for agent_file in "$agents_root"/*.md; do
    [ -f "$agent_file" ] || continue
    base="$(basename "$agent_file" .md)"
    if md="$(translate_agent_to_antigravity_md "$agent_file" "$plugin_name")"; then
      mkdir -p "$antigravity_agents_dir"
      printf '%s' "$md" > "$antigravity_agents_dir/$plugin_name-$base.md"
    else
      failed=1
    fi
  done
  return $failed
}

# --- Hook translation (Claude hooks.json -> Codex / Antigravity) ---
#
# Ported only for events confirmed to exist in the target's own vocabulary
# (developers.openai.com/codex/hooks; antigravity.google/docs/hooks/) —
# every other declared event is left alone, not guessed at. Both targets'
# hooks.json shapes were confirmed against official docs before writing
# any of this, following the same discipline as the agent translators.
#
# ~/.codex/hooks.json is one file shared by every plugin, and JSON has no
# comment syntax to mark a managed region the way config.toml's TOML
# merge does — so this tool treats that whole file as fully managed and
# regenerates it from the current roster every sync, same as it already
# treats the Antigravity plugins folder. A hand-written Codex hooks.json
# belongs in a different config layer (e.g. a project's own .codex/), not
# merged into this one.

CODEX_HOOK_EVENTS='["PreToolUse","PostToolUse","Stop","UserPromptSubmit","SessionStart"]'
ANTIGRAVITY_HOOK_EVENTS='["PreToolUse","PostToolUse","Stop"]'

# wrap_hook_for_env(): inlined identically into both translate_hooks_to_*_json
# functions below (matching this file's existing convention of duplicating
# each jq program's own logic rather than factoring it out — see the
# ${CLAUDE_PLUGIN_ROOT} gsub already duplicated the same way). Neither
# Codex's nor Antigravity's hooks.json schema has an env field (confirmed
# against their published docs) — only Claude Code sets CLAUDE_PLUGIN_ROOT
# as an actual environment variable for the spawned hook process, in
# addition to substituting it into the command string. A hook script that
# reads os.environ['CLAUDE_PLUGIN_ROOT'] itself (e.g. to import sibling
# modules, as hookify's hooks/*.py do for their core/ package) silently
# no-ops on both targets without this. Only rewrites the one confirmed-real
# shape (`python3 "<script>"`, nothing else appended) produced by
# substituting ${CLAUDE_PLUGIN_ROOT} into a plugin's own hooks.json —
# anything else is left untouched rather than guessed at. Kept as a
# single-quoted jq program (no bash interpolation of the def body) so
# jq's own $-variable syntax never has to survive bash double-quote
# escaping.
#
# The rewritten command adds two more quoted arguments (wrapper path,
# plugin dir) ahead of the script path — confirmed safe by inspecting the
# real Antigravity CLI binary directly (`strings` on the installed `agy`,
# v1.1.22): it tokenizes hook commands via
# github.com/carapace-sh/carapace-shlex's shlex.Split, a proper
# POSIX-quote-aware lexer (not a naive whitespace split, and no /bin/sh
# invocation was found either) — the same quoting semantics the
# already-working single-argument shape relied on, just with more
# arguments. `agy plugin validate` also accepts hookify's rewritten
# hooks.json outright. What's not verified from this repo's own tooling
# is the one thing no static inspection can prove: that Antigravity's
# hook *engine* actually invokes this command when a real tool-call event
# fires — bootstrap/verify-antigravity-live.sh/.ps1 exist to check that
# specifically, once run with a real authenticated `agy` session.

translate_hooks_to_codex_json() {
  local hooks_json_path="$1" plugin_dir="$2"
  local raw

  if ! raw="$(jq -c '.hooks // {}' "$hooks_json_path" 2>/dev/null)"; then
    echo "Failed to parse '$hooks_json_path' as JSON" >&2
    return 1
  fi

  jq -c --arg dir "$plugin_dir" --arg wrapper "$HOOK_ENV_WRAPPER" --argjson events "$CODEX_HOOK_EVENTS" '
    def wrap_hook_for_env($dir; $wrapper):
      . as $cmd
      | if ($cmd | test("^python3?\\s+\"[^\"]+\"$")) then
          ($cmd | capture("^python3?\\s+\"(?<script>[^\"]+)\"$")) as $m
          | "python3 " + ([$wrapper, $dir, $m.script] | map(tojson) | join(" "))
        else
          $cmd
        end;
    to_entries
    | map(select(.key as $k | $events | index($k)))
    | map(.value |= map(
        (if has("matcher") then {matcher: .matcher} else {} end)
        + {hooks: (.hooks | map(
            (if has("timeout") then {timeout: .timeout} else {} end)
            + {type: (.type // "command"),
               command: (((.command // "") | gsub("\\$\\{CLAUDE_PLUGIN_ROOT\\}"; $dir)) | wrap_hook_for_env($dir; $wrapper))}
          ))}
      ))
    | from_entries
  ' <<< "$raw"
}

merge_codex_hooks_json() {
  local hooks_config_path="$1"
  shift
  local combined="{}" plugin hooks_json

  while [ $# -ge 2 ]; do
    plugin="$1"; hooks_json="$2"
    shift 2
    if [ -z "$hooks_json" ] || [ "$hooks_json" = "{}" ]; then
      continue
    fi
    if ! combined="$(jq -c -n --argjson a "$combined" --argjson b "$hooks_json" '
      reduce ($b | to_entries[]) as $e ($a; .[$e.key] = ((.[$e.key] // []) + $e.value))
    ' 2>/dev/null)"; then
      echo "Failed to merge hooks for plugin '$plugin' into '$hooks_config_path'" >&2
      return 1
    fi
  done

  mkdir -p "$(dirname "$hooks_config_path")"
  jq -n --argjson h "$combined" '{hooks: $h}' > "$hooks_config_path"
}

translate_hooks_to_antigravity_json() {
  local hooks_json_path="$1" plugin_dir="$2" plugin_name="$3"
  local raw events

  if ! raw="$(jq -c '.hooks // {}' "$hooks_json_path" 2>/dev/null)"; then
    echo "Failed to parse '$hooks_json_path' as JSON" >&2
    return 1
  fi

  events="$(jq -c --arg dir "$plugin_dir" --arg wrapper "$HOOK_ENV_WRAPPER" --argjson events "$ANTIGRAVITY_HOOK_EVENTS" '
    def wrap_hook_for_env($dir; $wrapper):
      . as $cmd
      | if ($cmd | test("^python3?\\s+\"[^\"]+\"$")) then
          ($cmd | capture("^python3?\\s+\"(?<script>[^\"]+)\"$")) as $m
          | "python3 " + ([$wrapper, $dir, $m.script] | map(tojson) | join(" "))
        else
          $cmd
        end;
    to_entries
    | map(select(.key as $k | $events | index($k)))
    | map(.value |= map(
        (if has("matcher") then {matcher: .matcher} else {} end)
        + {hooks: (.hooks | map(
            (if has("timeout") then {timeout: .timeout} else {} end)
            + {type: (.type // "command"),
               command: (((.command // "") | gsub("\\$\\{CLAUDE_PLUGIN_ROOT\\}"; $dir)) | wrap_hook_for_env($dir; $wrapper))}
          ))}
      ))
    | from_entries
  ' <<< "$raw")"

  if [ "$events" = "{}" ]; then
    return 0
  fi
  jq -n --arg name "$plugin_name" --argjson events "$events" '{($name): $events}'
}

write_antigravity_hooks_json() {
  local plugin_staged_dir="$1"
  local json_content="$2"
  [ -n "$json_content" ] || return 0
  mkdir -p "$plugin_staged_dir"
  printf '%s' "$json_content" > "$plugin_staged_dir/hooks.json"
}

# --- Commands gap report ---
#
# Hand re-authoring ~100 commands across 39 plugins as skills is an
# explicit non-goal (re-forking on every upstream update, a content
# project rather than mechanical translation) — this just names what
# exists and isn't ported, per plugin, so the gap is visible rather than
# silently absent.

generate_command_gap_report() {
  local repo_root="$1" vendor_cache_dir="$2"
  local report_path="$repo_root/bootstrap/command-gap-report.md"
  local mp_json mp_name declared plugin resolved_json resolve_failure plugin_dir
  local commands_root cmd_file names total=0 plugins_with_commands=0

  {
    echo "# Command gap report"
    echo
    echo "Generated by bootstrap/sync from the declared external roster."
    echo "Commands are Claude Code slash-command definitions. Hand re-authoring"
    echo "them as skills for Codex/Antigravity is an explicit non-goal (see"
    echo "\`docs/superpowers/specs/2026-08-27-completion-milestone-design.md\`,"
    echo "Spec 4) — this names what exists and is not ported, per plugin,"
    echo "rather than leaving the gap silent."
    echo

    while IFS= read -r mp_json; do
      [ -n "$mp_json" ] || continue
      mp_name="$(echo "$mp_json" | jq -r '.name')"

      while IFS= read -r declared; do
        plugin="$(echo "$declared" | jq -r '.name')"
        [ -n "$plugin" ] || continue
        resolved_json="$(resolve_plugin_dir "$repo_root" "$vendor_cache_dir" "$mp_json" "$declared")"
        resolve_failure="$(echo "$resolved_json" | jq -r '.failure')"
        [ -z "$resolve_failure" ] || continue
        plugin_dir="$(echo "$resolved_json" | jq -r '.dir')"

        commands_root="$plugin_dir/commands"
        [ -d "$commands_root" ] || continue
        names=""
        for cmd_file in "$commands_root"/*.md; do
          [ -f "$cmd_file" ] || continue
          names="$names $(basename "$cmd_file" .md)"
          total=$((total + 1))
        done
        [ -n "$names" ] || continue
        plugins_with_commands=$((plugins_with_commands + 1))
        echo "- **$plugin** (from $mp_name):$names"
      done < <(get_declared_plugins "$mp_json")
    done < <(get_external_marketplaces_json "$repo_root")

    echo
    echo "$plugins_with_commands plugin(s), $total command(s) total — none ported."
  } > "$report_path"
}

normalize_external_mcp_servers_json() {
  local mcp_servers_json="$1"
  local plugin_dir="$2"

  jq -c --arg plugin_dir "$plugin_dir" '
    to_entries
    | map(
        .value |= (
          . as $server
          | del(.cwd, .env_vars, .type, ._meta)
          | if ($server | has("env_vars")) then
              .env = (
                (.env // {})
                + (reduce ($server.env_vars[]?) as $var ({}; . + (if env[$var] == null then {} else {($var): env[$var]} end)))
              )
            else . end
          | if ($server | has("cwd")) and ($server | has("args")) and (.args | type == "array") and ((.args | length) > 0) then
              .args[0] = (
                if (.args[0] | test("^(?:/|[A-Za-z]:\\\\|\\\\)")) then
                  .args[0]
                else
                  ($plugin_dir + "/" + (($server.cwd // ".") | tostring) + "/" + (.args[0] | tostring))
                end
              )
            else . end
          | if ($server | has("cwd")) then
              .env = ((.env // {}) + {CLAUDE_PLUGIN_ROOT: $plugin_dir})
            else . end
        )
      ) | from_entries
  ' <<< "$mcp_servers_json"
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

resolve_plugin_dir() {
  local repo_root="$1" vendor_cache_dir="$2" mp_json="$3" declared_plugin_json="$4"
  local name marketplace_name marketplace_dir src_json src_kind src_error src_path plugin_repos_dir result_json
  local dir pinned_commit resolved_sha save_err

  name="$(echo "$declared_plugin_json" | jq -r '.name // ""')"
  marketplace_name="$(echo "$mp_json" | jq -r '.name')"
  marketplace_dir="$vendor_cache_dir/$marketplace_name"
  src_json="$(get_plugin_source "$marketplace_dir" "$name")"
  src_kind="$(echo "$src_json" | jq -r '.kind // ""')"
  src_error="$(echo "$src_json" | jq -r '.error // ""')"

  if [ "$src_kind" = "missing" ]; then
    jq -n --arg failure "Plugin '$name' (from '$marketplace_name'): $src_error" '{dir:"",failure:$failure}'
    return 0
  fi

  if [ "$src_kind" = "inline" ]; then
    src_path="$(echo "$src_json" | jq -r '.path // ""')"
    dir="$marketplace_dir/${src_path#./}"
    if command -v cygpath >/dev/null 2>&1; then
      dir="$(cygpath -u "$dir" 2>/dev/null || printf '%s' "$dir")"
    fi
    if [ ! -d "$dir" ]; then
      jq -n --arg failure "Plugin '$name' (from '$marketplace_name'): declared inline path '$src_path' does not exist in the marketplace clone" '{dir:"",failure:$failure}'
      return 0
    fi
    printf '%s' "$dir" | jq -Rs '{dir: ., failure: ""}'
    return 0
  fi

  plugin_repos_dir="$vendor_cache_dir/_plugins"
  pinned_commit="$(echo "$declared_plugin_json" | jq -r '.resolvedCommit // ""')"
  result_json="$(sync_plugin_repo "$plugin_repos_dir" "$marketplace_name" "$name" "$src_json" "$pinned_commit")"
  if [ "$(echo "$result_json" | jq -r '.error // ""')" != "" ]; then
    echo "$result_json" | jq -c '{dir:"",failure:.error}'
    return 0
  fi
  dir="$(echo "$result_json" | jq -r '.dir // ""')"
  if command -v cygpath >/dev/null 2>&1; then
    dir="$(cygpath -u "$dir" 2>/dev/null || printf '%s' "$dir")"
  fi

  if [ "$(echo "$src_json" | jq -r '.sha // ""')" = "" ] \
    && [ "$(echo "$src_json" | jq -r '.ref // ""')" = "" ] \
    && [ -z "$pinned_commit" ]; then
    resolved_sha="$(echo "$result_json" | jq -r '.resolvedSha // ""')"
    if ! save_err="$(save_resolved_commit "$repo_root" "$marketplace_name" "$name" "$resolved_sha" 2>&1)"; then
      jq -n --arg failure "$save_err" '{dir:"",failure:$failure}'
      return 0
    fi
    echo "Pinned '$name' (from '$marketplace_name') to $resolved_sha" >&2
  fi

  printf '%s' "$dir" | jq -Rs '{dir: ., failure: ""}'
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

  # Antigravity's schema requires "serverUrl" for HTTP servers — "url" and
  # "httpUrl" are explicitly documented as unsupported legacy field names
  # (antigravity.google/docs/cli/mcp), confirmed directly against the real
  # CLI: `agy plugin validate` rejected a translated "url" field with
  # "must have either command or serverUrl". Renamed only for entries that
  # actually have "url" (the http-transport ones) — stdio entries (which
  # use "command") pass through unchanged.
  jq -n --argjson servers "$mcp_servers_json" '
    {mcpServers: ($servers | map_values(
      if has("url") then (. + {serverUrl: .url} | del(.url)) else . end
    ))}
  '
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

write_antigravity_plugin_json() {
  # Per https://antigravity.google/docs/ide/plugins/ and
  # .../cli/plugins/: a directory is only recognized as a plugin at all if
  # it has a plugin.json marker at its root, with a `name` matching
  # ^[a-zA-Z0-9-_]+$. This repo's own plugins already ship one (linked
  # wholesale); externally-vendored plugins are staged from scratch and
  # need one generated, or Antigravity's loader would not see them.
  local plugin_staged_dir="$1"
  local plugin_name="$2"
  mkdir -p "$plugin_staged_dir"
  jq -n --arg name "$plugin_name" '{name: $name}' > "$plugin_staged_dir/plugin.json"
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
  local codex_agents_dir="$5"
  local codex_hooks_config_path="$6"

  local failed=0
  local merge_args=()
  local hooks_merge_args=()
  local mp_json name plugin_line plugin resolved_json resolve_failure plugin_dir skills_root mcp_path mcp_servers toml hooks_path hooks_json

  while IFS= read -r mp_json; do
    [ -n "$mp_json" ] || continue
    name="$(echo "$mp_json" | jq -r '.name')"

    while IFS= read -r plugin_line; do
      plugin="$(echo "$plugin_line" | jq -r '.name')"
      [ -n "$plugin" ] || continue
      resolved_json="$(resolve_plugin_dir "$repo_root" "$vendor_cache_dir" "$mp_json" "$plugin_line")"
      resolve_failure="$(echo "$resolved_json" | jq -r '.failure')"
      if [ -n "$resolve_failure" ]; then
        echo "$resolve_failure" >&2
        failed=1
        continue
      fi
      plugin_dir="$(echo "$resolved_json" | jq -r '.dir')"

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
        elif normalized_mcp_servers="$(normalize_external_mcp_servers_json "$mcp_servers" "$plugin_dir")"; then
          if toml="$(mcp_json_to_codex_toml "$normalized_mcp_servers")"; then
          if [ -n "$toml" ]; then
            merge_args+=("$plugin" "$toml")
          fi
          else
            echo "Plugin '$plugin' (from '$name'): MCP translation to Codex TOML failed" >&2
            failed=1
          fi
        else
          echo "Plugin '$plugin' (from '$name'): failed to normalize '$mcp_path' for Codex" >&2
          failed=1
        fi
      fi

      if ! sync_plugin_agents_codex "$plugin_dir" "$plugin" "$codex_agents_dir"; then
        failed=1
      fi

      hooks_path="$plugin_dir/hooks/hooks.json"
      if [ -f "$hooks_path" ]; then
        if hooks_json="$(translate_hooks_to_codex_json "$hooks_path" "$plugin_dir")"; then
          hooks_merge_args+=("$plugin" "$hooks_json")
        else
          echo "Plugin '$plugin' (from '$name'): hook translation to Codex failed" >&2
          failed=1
        fi
      fi
    done < <(get_declared_plugins "$mp_json")
  done < <(get_external_marketplaces_json "$repo_root")

  if [ ${#merge_args[@]} -gt 0 ]; then
    if ! merge_codex_mcp_config "$codex_config_path" "${merge_args[@]}"; then
      echo "Failed to merge translated MCP servers into '$codex_config_path'" >&2
      failed=1
    fi
  fi

  if [ ${#hooks_merge_args[@]} -gt 0 ]; then
    if ! merge_codex_hooks_json "$codex_hooks_config_path" "${hooks_merge_args[@]}"; then
      echo "Failed to merge translated hooks into '$codex_hooks_config_path'" >&2
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
  local antigravity_agents_dir="$5"

  local failed=0
  local mp_json name plugin_line plugin resolved_json resolve_failure plugin_dir staged_plugin_dir
  local skills_source mcp_path mcp_servers config final_link hooks_path hooks_json

  while IFS= read -r mp_json; do
    [ -n "$mp_json" ] || continue
    name="$(echo "$mp_json" | jq -r '.name')"

    while IFS= read -r plugin_line; do
      plugin="$(echo "$plugin_line" | jq -r '.name')"
      [ -n "$plugin" ] || continue
      resolved_json="$(resolve_plugin_dir "$repo_root" "$vendor_cache_dir" "$mp_json" "$plugin_line")"
      resolve_failure="$(echo "$resolved_json" | jq -r '.failure')"
      if [ -n "$resolve_failure" ]; then
        echo "$resolve_failure" >&2
        failed=1
        continue
      fi
      plugin_dir="$(echo "$resolved_json" | jq -r '.dir')"
      staged_plugin_dir="$staged_dir/antigravity/$plugin"
      mkdir -p "$staged_plugin_dir"
      write_antigravity_plugin_json "$staged_plugin_dir" "$plugin"

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
        elif normalized_mcp_servers="$(normalize_external_mcp_servers_json "$mcp_servers" "$plugin_dir")"; then
          if config="$(mcp_json_to_antigravity_config "$normalized_mcp_servers")"; then
            write_antigravity_mcp_config "$staged_plugin_dir" "$config"
          else
            echo "Plugin '$plugin' (from '$name'): MCP translation to Antigravity config failed" >&2
            failed=1
            continue
          fi
        else
          echo "Plugin '$plugin' (from '$name'): failed to normalize '$mcp_path' for Antigravity" >&2
          failed=1
          continue
        fi
      fi

      hooks_path="$plugin_dir/hooks/hooks.json"
      if [ -f "$hooks_path" ]; then
        if hooks_json="$(translate_hooks_to_antigravity_json "$hooks_path" "$plugin_dir" "$plugin")"; then
          write_antigravity_hooks_json "$staged_plugin_dir" "$hooks_json"
        else
          echo "Plugin '$plugin' (from '$name'): hook translation to Antigravity failed" >&2
          failed=1
        fi
      fi

      final_link="$antigravity_plugins_dir/$plugin"
      if ! new_or_repair_symlink "$final_link" "$staged_plugin_dir"; then
        echo "Plugin '$plugin' (from '$name'): failed to link into Antigravity plugins dir" >&2
        failed=1
      fi

      # Agents are not a plugin-level file per Antigravity's own docs (only
      # skills/, mcp_config.json, hooks.json, rules/ are) — they live in a
      # separate global directory, so they're written there directly
      # rather than staged alongside the plugin.
      if ! sync_plugin_agents_antigravity "$plugin_dir" "$plugin" "$antigravity_agents_dir"; then
        failed=1
      fi
    done < <(get_declared_plugins "$mp_json")
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
      pname="$(echo "$plugin_line" | jq -r '.name')"
      [ -n "$pname" ] || continue
      if ! claude plugin install "$pname@$name"; then
        echo "claude plugin install '$pname@$name' failed" >&2
        failed=1
      fi
    done < <(get_declared_plugins "$mp_json")
  done < <(get_external_marketplaces_json "$repo_root")

  return $failed
}

# --- claude.ai account surface ---
#
# No upload API exists for account-level Skills, so this module can only
# stage upload-ready bundles and print an ordered checklist against the
# last confirmed state — never touch the account itself.

get_account_manifest_json() {
  local repo_root="$1"
  local path="$repo_root/bootstrap/account-manifest.json"
  if [ ! -f "$path" ]; then
    jq -n '{skills: [], connectors: []}'
    return 0
  fi
  jq -c '{skills: (.skills // []), connectors: (.connectors // [])}' "$path"
}

get_account_last_applied_json() {
  local repo_root="$1"
  local path="$repo_root/bootstrap/account-manifest.last-applied.json"
  if [ ! -f "$path" ]; then
    jq -n '{skills: [], connectors: []}'
    return 0
  fi
  jq -c '{skills: (.skills // []), connectors: (.connectors // [])}' "$path"
}

stage_account_skill_bundle() {
  local repo_root="$1" staged_dir="$2" skill_name="$3" source_rel_path="$4"
  local source_dir="$repo_root/$source_rel_path"
  if [ ! -d "$source_dir" ]; then
    echo "Account skill '$skill_name': declared source '$source_rel_path' does not exist" >&2
    return 1
  fi
  local dest_dir="$staged_dir/$skill_name"
  rm -rf "$dest_dir"
  mkdir -p "$dest_dir"
  # A real copy, not a live link — this bundle is meant to be uploaded
  # through the claude.ai UI, which does not resolve symlinks.
  cp -R "$source_dir/." "$dest_dir/"
}

print_account_checklist() {
  local repo_root="$1"
  local current last_applied adds removes

  current="$(get_account_manifest_json "$repo_root")"
  last_applied="$(get_account_last_applied_json "$repo_root")"

  adds="$(jq -rn --argjson cur "$current" --argjson last "$last_applied" '
    ([$cur.skills[]?.name] - [$last.skills[]?.name] | map("skill: " + .)) +
    ([$cur.connectors[]?.name] - [$last.connectors[]?.name] | map("connector: " + .))
    | .[]')"
  removes="$(jq -rn --argjson cur "$current" --argjson last "$last_applied" '
    ([$last.skills[]?.name] - [$cur.skills[]?.name] | map("skill: " + .)) +
    ([$last.connectors[]?.name] - [$cur.connectors[]?.name] | map("connector: " + .))
    | .[]')"

  if [ -z "$adds" ] && [ -z "$removes" ]; then
    echo "claude.ai account: up to date with last-applied state."
    return 0
  fi

  echo "claude.ai account checklist (manual — no upload API exists):"
  if [ -n "$adds" ]; then
    echo "  ADD:"
    echo "$adds" | sed 's/^/    - /'
  fi
  if [ -n "$removes" ]; then
    echo "  REMOVE:"
    echo "$removes" | sed 's/^/    - /'
  fi
  echo "  After applying by hand on claude.ai, run: bootstrap/sync.sh --confirm-account-applied"
}

sync_account_bundles() {
  local repo_root="$1" staged_dir="$2"
  local manifest_json failed=0 skill_json name source

  manifest_json="$(get_account_manifest_json "$repo_root")"
  mkdir -p "$staged_dir"

  while IFS= read -r skill_json; do
    [ -n "$skill_json" ] || continue
    name="$(echo "$skill_json" | jq -r '.name')"
    source="$(echo "$skill_json" | jq -r '.source')"
    if ! stage_account_skill_bundle "$repo_root" "$staged_dir" "$name" "$source"; then
      failed=1
    fi
  done < <(echo "$manifest_json" | jq -c '.skills[]?')

  print_account_checklist "$repo_root"

  return $failed
}

confirm_account_applied() {
  local repo_root="$1"
  local manifest_path="$repo_root/bootstrap/account-manifest.json"
  local last_applied_path="$repo_root/bootstrap/account-manifest.last-applied.json"

  if [ ! -f "$manifest_path" ]; then
    echo "cannot confirm: '$manifest_path' does not exist" >&2
    return 1
  fi
  cp "$manifest_path" "$last_applied_path"
  echo "Recorded current account-manifest.json as last-applied."
}

if [ "${1:-}" = "--import" ]; then
  return 0 2>/dev/null || exit 0
fi

if [ "${1:-}" = "--confirm-account-applied" ]; then
  confirm_account_applied "$REPO_ROOT"
  exit $?
fi

# --- Capability detection ---
#
# Each stage below is gated on whether its target surface actually exists on
# this machine. A capability function returns 0 (present) silently, or
# returns non-zero and prints the reason to stdout — run_stage captures that
# reason and reports it as a skip, rather than the stage failing or
# silently doing nothing. This is the "stage" interface later specs
# register new stages against: one capability function, one run function,
# one run_stage call.

codex_capability() {
  if [ -n "$CODEX_SKILLS_DIR_OVERRIDDEN" ]; then
    return 0
  fi
  if [ -d "$HOME/.codex" ]; then
    return 0
  fi
  echo "no '$HOME/.codex' directory found and CODEX_SKILLS_DIR was not explicitly set"
  return 1
}

antigravity_capability() {
  if [ -n "$ANTIGRAVITY_PLUGINS_DIR_OVERRIDDEN" ]; then
    return 0
  fi
  if [ -d "$HOME/.gemini" ]; then
    return 0
  fi
  echo "no '$HOME/.gemini' directory found and ANTIGRAVITY_PLUGINS_DIR was not explicitly set"
  return 1
}

vendor_cache_capability() {
  if codex_capability >/dev/null 2>&1 || antigravity_capability >/dev/null 2>&1; then
    return 0
  fi
  echo "neither Codex nor Antigravity capability detected"
  return 1
}

claude_code_capability() {
  if [ -n "$SKIP_CLAUDE_CODE" ]; then
    echo "SKIP_CLAUDE_CODE is set"
    return 1
  fi
  if command -v claude >/dev/null 2>&1; then
    return 0
  fi
  echo "'claude' CLI not found on PATH"
  return 1
}

always_capability() {
  return 0
}

# --- Stage runner ---

STAGE_OK=()
STAGE_SKIPPED=()
STAGE_FAILED=()

run_stage() {
  local stage_name="$1" capability_fn="$2" run_fn="$3"
  shift 3
  local reason

  if ! reason="$("$capability_fn")"; then
    echo "Skipping stage '$stage_name': $reason"
    STAGE_SKIPPED+=("$stage_name: $reason")
    return 0
  fi

  if ! "$run_fn" "$@"; then
    echo "Stage '$stage_name' failed (see messages above)." >&2
    STAGE_FAILED+=("$stage_name")
    overall_failed=1
    return 1
  fi
  STAGE_OK+=("$stage_name")
  return 0
}

# --- Run stages ---

VENDOR_CACHE_DIR="$REPO_ROOT/.vendor-cache"
STAGED_DIR="$VENDOR_CACHE_DIR/_staged"
CODEX_CONFIG_PATH="$HOME/.codex/config.toml"
CODEX_HOOKS_CONFIG_PATH="$HOME/.codex/hooks.json"

overall_failed=0

run_stage "codex-skills" codex_capability sync_codex_skills "$REPO_ROOT" "$CODEX_SKILLS_DIR" || true
run_stage "antigravity-plugins" antigravity_capability sync_antigravity_plugins "$REPO_ROOT" "$ANTIGRAVITY_PLUGINS_DIR" || true

run_stage "vendor-cache" vendor_cache_capability sync_vendor_cache "$REPO_ROOT" "$VENDOR_CACHE_DIR" || true
run_stage "command-gap-report" vendor_cache_capability generate_command_gap_report "$REPO_ROOT" "$VENDOR_CACHE_DIR" || true
run_stage "external-codex-content" codex_capability sync_external_codex_content "$REPO_ROOT" "$VENDOR_CACHE_DIR" "$CODEX_SKILLS_DIR" "$CODEX_CONFIG_PATH" "$CODEX_AGENTS_DIR" "$CODEX_HOOKS_CONFIG_PATH" || true
run_stage "external-antigravity-content" antigravity_capability sync_external_antigravity_content "$REPO_ROOT" "$VENDOR_CACHE_DIR" "$STAGED_DIR" "$ANTIGRAVITY_PLUGINS_DIR" "$ANTIGRAVITY_AGENTS_DIR" || true

run_stage "claude-code-marketplace" claude_code_capability sync_claude_code_marketplace "$REPO_ROOT" || true
run_stage "account-bundles" always_capability sync_account_bundles "$REPO_ROOT" "$VENDOR_CACHE_DIR/_staged/claude-ai" || true

echo
echo "--- Sync summary ---"
if [ ${#STAGE_OK[@]} -gt 0 ]; then
  echo "Ran: ${STAGE_OK[*]}"
fi
if [ ${#STAGE_SKIPPED[@]} -gt 0 ]; then
  echo "Skipped:"
  for s in "${STAGE_SKIPPED[@]}"; do
    echo "  - $s"
  done
fi
if [ ${#STAGE_FAILED[@]} -gt 0 ]; then
  echo "Failed: ${STAGE_FAILED[*]}"
fi

if [ "$overall_failed" -ne 0 ]; then
  echo "Sync completed with failures (see messages above)." >&2
  exit 1
fi

echo "Sync complete."
