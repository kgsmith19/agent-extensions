#!/usr/bin/env bash
# Builds a local git repo standing in for an external marketplace, with 5
# fixture plugins covering: skill-only (zero MCP servers), stdio MCP,
# HTTP MCP, a semantically-malformed MCP server (neither command nor url),
# and a syntactically-invalid .mcp.json (JSON parse error).
new_fixture_marketplace() {
  local dest_dir="$1"

  rm -rf "$dest_dir"
  mkdir -p "$dest_dir"

  mkdir -p "$dest_dir/alpha-skills/skills/greet"
  cat > "$dest_dir/alpha-skills/skills/greet/SKILL.md" <<'EOF'
---
name: greet
description: Says hello. Fixture skill for agent-extensions sync tests.
---

Say hello to the user.
EOF

  mkdir -p "$dest_dir/beta-mcp-stdio"
  cat > "$dest_dir/beta-mcp-stdio/.mcp.json" <<'EOF'
{
  "mcpServers": {
    "fixture-stdio": {
      "command": "node",
      "args": ["server.js", "--port", "0"],
      "env": { "FIXTURE_MODE": "stdio" }
    }
  }
}
EOF

  mkdir -p "$dest_dir/gamma-mcp-http"
  cat > "$dest_dir/gamma-mcp-http/.mcp.json" <<'EOF'
{
  "mcpServers": {
    "fixture-http": {
      "url": "https://fixture.example.com/mcp",
      "headers": { "Authorization": "Bearer FIXTURE_TOKEN" }
    }
  }
}
EOF

  mkdir -p "$dest_dir/delta-malformed"
  cat > "$dest_dir/delta-malformed/.mcp.json" <<'EOF'
{
  "mcpServers": {
    "fixture-bad": {
      "transportKind": "carrier-pigeon"
    }
  }
}
EOF

  mkdir -p "$dest_dir/epsilon-invalid-json"
  cat > "$dest_dir/epsilon-invalid-json/.mcp.json" <<'EOF'
{ "mcpServers": { "broken": } }
EOF

  (
    cd "$dest_dir"
    git init -q
    git config user.email "fixture@agent-extensions.test"
    git config user.name "agent-extensions fixture"
    git add -A
    git commit -q -m "Fixture marketplace content"
    git rev-parse HEAD
  )
}
