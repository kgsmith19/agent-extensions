# Kilo bounded-task canary (#36, R3)

Proves, against the real pinned Kilo CLI, that a bounded task is enforced by
Kilo's own parser, permission engine and launcher. Four scenarios: an allowed
edit, a forbidden action denied by rule, a cold resume of a session, and a
mid-stream cancellation. The runner writes `receipt.json`.

## What is real and what is scripted

| Part | Status |
| --- | --- |
| `@kilocode/cli@7.8.10` (`kilo --version` must print `7.8.10`) | real, pinned |
| Event parser: `kilo run --format json` JSON event stream | real |
| Permission engine (`permission` rules in `kilo.json`) | real |
| Tool dispatch and tool-state errors | real |
| Session storage and `kilo run --session <id>` resume | real |
| Launcher: npm bin wrapper and platform binary, signal handling | real |
| LLM provider | **scripted** (`scripted_model.py`), the only double |

The scripted model is an OpenAI-compatible server on `127.0.0.1`. It emits one
tool call on the first request, then answers `done` once a tool result is in
the request. A `stream-slow` mode streams text deltas for the cancel scenario.
Each request is logged, so the runner can prove what the model received.

The receipt records this as `provenance.model = "scripted-local/m1 (deterministic lane)"`
and `provenance.double` names the scripted server.

## Files

- `scripted_model.py`: the deterministic model server (stdlib only).
- `run_canary.py`: runs the scenarios against the CLI, judges them, writes the receipt.
- `kilo.json.template`: the permission rules (deny by default; one edit path and one bash prefix allowed).
- `live-lane.json`: non-secret config for the live lane. Empty identity means not provisioned.

## Run it locally

Node 22.19.0 was used. Install the pinned CLI outside the repo:

    npm install --no-audit --no-fund --prefix /path/to/kilo-prefix @kilocode/cli@7.8.10
    python3 canary/kilo/run_canary.py \
        --kilo-bin /path/to/kilo-prefix/node_modules/.bin/kilo \
        --out receipt.json

Exit status is 0 when no scenario fails (gaps are listed in the receipt), 1 when
a scenario fails or the receipt is malformed, and 2 on setup errors such as a
wrong `kilo --version`.

Tests:

    python -m pytest -q tests/test_kilo_canary.py          # unit, no CLI, no network
    KILO_BIN=/path/to/kilo-prefix/node_modules/.bin/kilo \
        python -m pytest -q tests/test_kilo_canary.py      # adds the real-CLI run

## Scenarios and verdicts

- **allow**: a `write` to `allowed.txt` must complete, and the file must hold
  exactly `hello\n`. Pass requires both.
- **deny**: a `write` to `forbidden.txt` must fail with a tool error that cites
  a `deny` permission rule, and the file must not exist. A denial without a
  cited rule is a fail ("wrong reason"). A tool that is not offered is also a
  fail, because it is missing setup, not a rule.
- **deny (bash)**: a `printf` command is denied by the `bash` rule
  (`echo *` is the only allowed prefix). The same rule-citation check applies.
  Bash is kept partly allowed on purpose: a fully denied tool is removed from
  the tool list the model sees, and that denial cites no rule.
- **resume**: turn 1 writes `allowed.txt`. Turn 2 is a new CLI process and a new
  model process, run with `--session <id>`. Pass requires the same session id on
  every event, prior messages (including the tool result) in the request the
  model received, a different model process, and an identical final workspace
  hash. "Same receipt" means the same session id and the same workspace state.
- **cancel**: a streaming run is interrupted with SIGINT once three chunks have
  streamed. SIGTERM, then SIGKILL, follow after 5 s grace each. Because Kilo
  emits no cancellation event, the launcher (`Runner.run_cancel`) issues its own
  receipt (`evidence.launcher_receipt`, verdict `cancelled-acknowledged`, issuer
  `launcher`, #92): the signals sent, the process-group exit status (terminated,
  returncode, group empty after exit), elapsed time, and a byte-exact workspace
  snapshot before and after the cancel point with any `partial_effects`. The
  scenario passes only on a consistent launcher receipt (`acknowledged_by:
  launcher`); without one it stays a **gap**, never a pass. A cancelled run
  reported as a successful stop is a fail.

## Findings from the real CLI (7.8.10)

- Permission patterns match the absolute path. A literal `allowed.txt` rule
  does not match. Use a glob such as `**/allowed.txt`.
- A denial is reported as a tool event with `state.status = "error"`, and the
  error text plus `metadata.approval.rule` cite the rule. `kilo run` still exits
  0, so the verdict must come from the event stream.
- Cold resume works: `kilo run --session <id>` in a new process continues the
  same session, and the prior messages are in the request.
- SIGINT and SIGTERM end the run at once. The event stream holds only the
  `step_start` event; there is no cancellation event. This is why the
  upstream `cancel-ack` gap stays declared even when the launcher receipt passes
  the scenario. If a later pinned Kilo emits one, its event wins first.

## Declared gaps

Every entry in `KiloAdapter.gaps()` (`agent_extensions/sync/harness.py`) is
listed in the receipt: hooks, plugins, agents, commands, connectors, mcp,
subagent-model-tiers. The canary does not exercise them. It adds `cancel-ack`
on top.

## The live lane and how it differs

The workflow's live job runs only on `workflow_dispatch` with `lane=live`. It
never runs in the PR Gate. It is advisory.

- It reads `live-lane.json`. An empty `infisical_identity_id` prints a notice
  ("owner action: create Infisical OIDC identity for this workflow") and
  succeeds without running. This is the current state: it is not provisioned.
- When provisioned, it fetches `DEV_KILO_API_KEY` through Infisical OIDC and
  runs with no scripted server. The real provider decides what the model does,
  so the scenarios depend on the model's behaviour. Its receipt is uploaded
  and its step is `continue-on-error`.
- Unverified: the `Infisical/secrets-action` input names used in the workflow
  (`method`, `identity-id`, `project-slug`, `env-slug`, `secret-path`,
  `export-type`) and how Kilo reads the provider key (the runner exports
  `KILO_API_KEY`). Both must be checked by the owner before the lane is enabled.
