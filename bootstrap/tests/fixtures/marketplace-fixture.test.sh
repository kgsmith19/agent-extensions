#!/usr/bin/env bash
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=/dev/null
source "$HERE/marketplace-fixture.sh"

failures=()
SCRATCH="$(mktemp -d)"

REPO_DIR="$SCRATCH/plugin-repo"
REPO_SHA="$(new_fixture_plugin_repo "$REPO_DIR")"
MP_DIR="$SCRATCH/marketplace"
MP_SHA="$(new_fixture_marketplace "$MP_DIR" "$REPO_DIR" "$REPO_SHA")"

if [[ ! "$REPO_SHA" =~ ^[0-9a-f]{40}$ ]]; then
  failures+=("plugin-repo sha malformed")
fi
if [[ ! "$MP_SHA" =~ ^[0-9a-f]{40}$ ]]; then
  failures+=("marketplace sha malformed")
fi

MANIFEST_PATH="$MP_DIR/.claude-plugin/marketplace.json"
if [ ! -f "$MANIFEST_PATH" ]; then
  failures+=("fixture has no .claude-plugin/marketplace.json")
else
  for n in alpha-skills beta-mcp-stdio gamma-mcp-http delta-malformed epsilon-invalid-json zeta-repo-pinned eta-repo-subpath theta-repo-unpinned; do
    present="$(jq --arg n "$n" '.plugins | any(.name == $n)' "$MANIFEST_PATH" | tr -d '\r')"
    if [ "$present" != "true" ]; then
      failures+=("manifest missing plugin '$n'")
    fi
  done
  omega_present="$(jq '.plugins | any(.name == "omega-absent")' "$MANIFEST_PATH" | tr -d '\r')"
  if [ "$omega_present" = "true" ]; then
    failures+=("'omega-absent' must NOT be in the manifest (negative case)")
  fi

  alpha_source="$(jq -r '.plugins[] | select(.name == "alpha-skills") | .source' "$MANIFEST_PATH" | tr -d '\r')"
  if [ "$alpha_source" != "./plugins/alpha-skills" ]; then
    failures+=("alpha-skills source wrong")
  fi

  gamma_source="$(jq -r '.plugins[] | select(.name == "gamma-mcp-http") | .source' "$MANIFEST_PATH" | tr -d '\r')"
  if [ "$gamma_source" != "./external_plugins/gamma-mcp-http" ]; then
    failures+=("gamma-mcp-http must live under external_plugins/")
  fi

  zeta_source_kind="$(jq -r '.plugins[] | select(.name == "zeta-repo-pinned") | .source.source' "$MANIFEST_PATH" | tr -d '\r')"
  if [ "$zeta_source_kind" != "url" ]; then
    failures+=("zeta must be a url source")
  fi

  zeta_sha="$(jq -r '.plugins[] | select(.name == "zeta-repo-pinned") | .source.sha' "$MANIFEST_PATH" | tr -d '\r')"
  if [ "$zeta_sha" != "$REPO_SHA" ]; then
    failures+=("zeta sha must match the plugin repo sha")
  fi

  eta_path="$(jq -r '.plugins[] | select(.name == "eta-repo-subpath") | .source.path' "$MANIFEST_PATH" | tr -d '\r')"
  if [ "$eta_path" != "nested/eta" ]; then
    failures+=("eta must declare path 'nested/eta'")
  fi

  theta_has_sha="$(jq '.plugins[] | select(.name == "theta-repo-unpinned") | .source | has("sha")' "$MANIFEST_PATH" | tr -d '\r')"
  if [ "$theta_has_sha" = "true" ]; then
    failures+=("theta must declare NO sha (unpinned case)")
  fi
fi

if [ ! -f "$MP_DIR/plugins/alpha-skills/skills/greet/SKILL.md" ]; then
  failures+=("alpha-skills content not at plugins/alpha-skills")
fi
if [ ! -f "$MP_DIR/external_plugins/gamma-mcp-http/.mcp.json" ]; then
  failures+=("gamma content not at external_plugins/gamma-mcp-http")
fi
if [ ! -f "$REPO_DIR/skills/remote-greet/SKILL.md" ]; then
  failures+=("plugin repo missing root skill")
fi
if [ ! -f "$REPO_DIR/nested/eta/skills/eta-greet/SKILL.md" ]; then
  failures+=("plugin repo missing nested/eta skill")
fi

ALLOW_ANY="$(git -C "$REPO_DIR" config --get uploadpack.allowAnySHA1InWant)"
if [ "$ALLOW_ANY" != "true" ]; then
  failures+=("plugin repo must set uploadpack.allowAnySHA1InWant=true")
fi

rm -rf "$SCRATCH"

if [ ${#failures[@]} -gt 0 ]; then
  echo "FAIL: marketplace fixture"
  printf '  - %s\n' "${failures[@]}"
  exit 1
fi
echo "PASS: fixture builds real marketplace layout with manifest and all source kinds"
