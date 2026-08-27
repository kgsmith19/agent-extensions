#!/usr/bin/env bash
new_fixture_skill() {
  local skills_root="$1" name="$2" description="$3"
  mkdir -p "$skills_root/$name"
  cat > "$skills_root/$name/SKILL.md" <<EOF
---
name: $name
description: $description
---

Fixture skill body.
EOF
}

new_fixture_plugin_repo() {
  local dest_dir="$1"
  rm -rf "$dest_dir"; mkdir -p "$dest_dir"
  new_fixture_skill "$dest_dir/skills" "remote-greet" "Fixture skill served from an external plugin repo."
  new_fixture_skill "$dest_dir/nested/eta/skills" "eta-greet" "Fixture skill in a subdirectory of an external plugin repo."
  ( cd "$dest_dir" \
    && git init -q \
    && git config user.email "fixture@agent-extensions.test" \
    && git config user.name "agent-extensions fixture" \
    && git config uploadpack.allowAnySHA1InWant true \
    && git add -A && git commit -q -m "Fixture external plugin repo" \
    && git rev-parse HEAD )
}

new_fixture_marketplace() {
  local dest_dir="$1" plugin_repo_dir="$2" plugin_repo_sha="$3"
  rm -rf "$dest_dir"; mkdir -p "$dest_dir"

  new_fixture_skill "$dest_dir/plugins/alpha-skills/skills" "greet" "Says hello. Fixture skill for agent-extensions sync tests."

  mkdir -p "$dest_dir/plugins/beta-mcp-stdio"
  cat > "$dest_dir/plugins/beta-mcp-stdio/.mcp.json" <<'MCPEOF'
{
  "mcpServers": {
    "fixture-stdio": {
      "command": "node",
      "args": ["server.js", "--port", "0"],
      "env": { "FIXTURE_MODE": "stdio" }
    }
  }
}
MCPEOF

  mkdir -p "$dest_dir/external_plugins/gamma-mcp-http"
  cat > "$dest_dir/external_plugins/gamma-mcp-http/.mcp.json" <<'MCPEOF'
{
  "mcpServers": {
    "fixture-http": {
      "url": "https://fixture.example.com/mcp",
      "headers": { "Authorization": "Bearer FIXTURE_TOKEN" }
    }
  }
}
MCPEOF

  mkdir -p "$dest_dir/plugins/delta-malformed"
  cat > "$dest_dir/plugins/delta-malformed/.mcp.json" <<'MCPEOF'
{
  "mcpServers": {
    "fixture-bad": {
      "transportKind": "carrier-pigeon"
    }
  }
}
MCPEOF

  mkdir -p "$dest_dir/plugins/epsilon-invalid-json"
  printf '%s' '{ "mcpServers": { "broken": } }' > "$dest_dir/plugins/epsilon-invalid-json/.mcp.json"

  local repo_url="file:///${plugin_repo_dir#/}"
  mkdir -p "$dest_dir/.claude-plugin"
  jq -n --arg url "$repo_url" --arg sha "$plugin_repo_sha" '{
    name: "fixture-marketplace",
    plugins: [
      { name: "alpha-skills",         source: "./plugins/alpha-skills" },
      { name: "beta-mcp-stdio",       source: "./plugins/beta-mcp-stdio" },
      { name: "gamma-mcp-http",       source: "./external_plugins/gamma-mcp-http" },
      { name: "delta-malformed",      source: "./plugins/delta-malformed" },
      { name: "epsilon-invalid-json", source: "./plugins/epsilon-invalid-json" },
      { name: "zeta-repo-pinned",     source: { source: "url", url: $url, sha: $sha } },
      { name: "eta-repo-subpath",     source: { source: "url", url: $url, sha: $sha, path: "nested/eta" } },
      { name: "theta-repo-unpinned",  source: { source: "url", url: $url } }
    ]
  }' > "$dest_dir/.claude-plugin/marketplace.json"

  ( cd "$dest_dir" \
    && git init -q \
    && git config user.email "fixture@agent-extensions.test" \
    && git config user.name "agent-extensions fixture" \
    && git config uploadpack.allowAnySHA1InWant true \
    && git add -A && git commit -q -m "Fixture marketplace content" \
    && git rev-parse HEAD )
}
