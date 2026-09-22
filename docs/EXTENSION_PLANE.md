# The extension plane — CLI first, MCP optional

How to make a capability callable from *every* harness without adopting any
provider's plugin format.

## Principle

A capability is a **CLI**. MCP is an adapter, never the contract.

The reason is concrete: this setup runs on pi, and **pi has no MCP support**.
Claude Code, Codex, and Antigravity do. If a capability is defined as an MCP
server first, it is invisible to pi. If it is defined as a CLI first, every
harness that can run a shell command can call it, and the MCP-capable harnesses
get an adapter on top.

```
capability  ->  CLI (neutral contract)  ->  { any harness shell }
                       |
                       +-> MCP server   ->  { Claude, Codex, Antigravity }
                       +-> HTTP/OpenAPI ->  { anything }
```

## Bridges

| Direction | Tool | Status | Use |
|---|---|---|---|
| MCP -> CLI | [`mcporter`](https://github.com/openclaw/mcporter) | **installed** (`npm i -g mcporter`, Node 24+) | `mcporter list`, `mcporter call <server>.<tool> k=v`, `mcporter generate-cli` to emit standalone CLIs |
| CLI -> MCP | [`any-cli-mcp-server`](https://github.com/eirikb/any-cli-mcp-server) | documented | wrap any CLI as MCP tools parsed from `--help` |
| MCP -> HTTP/OpenAPI | [`open-webui/mcpo`](https://github.com/open-webui/mcpo) | documented | expose MCP servers as OpenAPI |
| MCP -> CLI (Go) | [`f/mcptools`](https://github.com/f/mcptools) | no Windows binary | alternative to mcporter on Unix |

Verified live:

```sh
mcporter list https://mcp.context7.com/mcp --brief
# 2 tools · 217ms · HTTP https://mcp.context7.com/mcp
```

## How the continuity capsule follows this

The provider-neutral continuity system (`agent_extensions/continuity/`) is the
reference implementation of the rule above:

- **Contract:** a CLI (`capture`/`render`/`status`/`clear`) plus a plain-JSON
  store. No SDK.
- **Adapters:** pi extension and Claude hooks call the same CLI. Neither owns
  the state.
- **Optional MCP:** because the contract is a CLI, it can be exposed over MCP
  with `any-cli-mcp-server` without changing the core.

## Rules

1. Define new capabilities as CLIs with a stable, documented interface.
2. Add MCP only as a thin adapter over the CLI, never as the source of truth.
3. Keep provider-specific wiring in the smallest possible file (the adapter).
4. If a capability only ships as a provider plugin, vendor it into
   `agent-extensions` and render it outward; do not let a live marketplace be
   the authority.
