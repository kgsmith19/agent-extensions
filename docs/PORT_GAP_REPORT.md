# Gap Report — Stage 22 Mechanical Port (generated 2026-09-18)

Structural port of every plugin to every provider via
`agent_extensions/sync/porting.py::port_plugin`. Shell fixes and tests
untouched (`sync.test.ps1` PASS, `hook-translate.test.ps1` PASS).

## Per-plugin / per-provider counts

| Plugin | claude | codex | antigravity | local |
|---|---|---|---|---|
| anthropic-product-skills | 0 agents, 0 hooks, 0 commands, 0 mcp | same | same | same |
| general-skills | 3 agents, 0 hooks, 0 commands, 0 mcp | same | same | same |

(Totals: 3 agents ported everywhere — analyzer, comparator, grader.
No hooks.json, commands/, or .mcp.json ship in this repo's own plugins,
so there is nothing structural to translate or drop.)

## Gaps

No unrepresentable cases in the current tree — zero gaps recorded on all
four providers. The gap machinery (`GapReport`, `translate_hook_event`,
`port_mcp_config` legacy-url flag, malformed-JSON handling) is proven by
`tests/test_porting.py` fixtures and stands ready for external-roster
content (Stages 23–24).

Known standing gaps (owned elsewhere, not silent):

- External-roster slash-commands (31 across 10 plugins) are intentionally
  not ported — see `bootstrap/command-gap-report.md` ( invokes the
  underlying skill directly).
- Claude hook payload bridging on Codex/Antigravity goes through
  `bootstrap/hook_env_wrapper.py`; hook inheritance stays
  explicitly unsupported off Claude (Stage 19 UNSUPPORTED table).
- Hooks are never a security trust root (`child_inherits_hook` returns
  False off Claude).

## Reason + alternative for every gap class

| Kind | Reason | Alternative |
|---|---|---|
| hook (off-vocab event) | event not in provider's documented vocabulary | handle at bootstrap/report time; no runtime hook installed |
| hook (malformed JSON) | hooks.json invalid | fix upstream; nothing installed |
| command | slash-command has no structural equivalent | invoke the underlying skill directly |
| mcp (legacy url) | `url` unsupported on codex/antigravity | use `serverUrl` (ported automatically) |
| mcp (malformed JSON) | .mcp.json invalid | fix upstream; nothing installed |
| agent (hook refs) | agent references Claude-native hook semantics | route through `hook_env_wrapper.py` |
