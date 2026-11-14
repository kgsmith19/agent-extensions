"""Run one command with named secrets in ITS environment only: the provider-neutral
equivalent of `infisical run` / `op run` / `doppler run`.

    ae secret-run --env CF_TOKEN=/:CLOUDFLARE_API_TOKEN [--env NAME=/path:KEY ...] -- <command> [args...]

Rules held here:

- The value goes into the child's environment and nowhere else: never stdout, a
  file, a log, or this process's own environment.
- The child's stdout and stderr pass through with every injected value replaced by
  `[REDACTED:<NAME>]`, so a command that echoes its token still leaks nothing.
- The store is the first configured `SecretStore` (inventory.default_stores), so
  the same command works with any store that has an adapter.
- Exit code is the child's; 2 means a spec was malformed or a secret was not found.
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from collections.abc import Mapping, Sequence

from agent_extensions.inventory import SecretStore, default_stores

SPEC = re.compile(r"^(?P<var>[A-Z_][A-Z0-9_]*)=(?P<path>/[^:]*):(?P<key>[A-Za-z0-9_.-]+)$")


def parse_specs(specs: Sequence[str]) -> list[tuple[str, str, str]]:
    out = []
    for spec in specs:
        match = SPEC.match(spec)
        if not match:
            raise ValueError(f"bad --env spec {spec!r}; expected NAME=/path:KEY")
        out.append((match["var"], match["path"], match["key"]))
    return out


def redact(text: str, values: Mapping[str, str]) -> str:
    for var, value in values.items():
        if value:
            text = text.replace(value, f"[REDACTED:{var}]")
    return text


def run(
    specs: Sequence[str],
    command: Sequence[str],
    store: SecretStore,
    env: Mapping[str, str],
    runner=subprocess.run,
) -> tuple[int, str, str]:
    values = {var: store.get(path, key) for var, path, key in parse_specs(specs)}
    child_env = {**env, **values}
    done = runner(list(command), env=child_env, capture_output=True, text=True)
    return done.returncode, redact(done.stdout or "", values), redact(done.stderr or "", values)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="ae secret-run", description=__doc__.split("\n\n")[0])
    parser.add_argument("--env", action="append", default=[], metavar="NAME=/path:KEY")
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    if not command or not args.env:
        parser.error("need at least one --env and a command after --")
    stores = default_stores(os.environ)
    if not stores:
        print("secret-run: no secret store identity in the environment (see `ae inventory`)", file=sys.stderr)
        return 2
    try:
        code, out, err = run(args.env, command, stores[0], os.environ)
    except (ValueError, LookupError) as error:
        print(f"secret-run: {error}", file=sys.stderr)
        return 2
    sys.stdout.write(out)
    sys.stderr.write(err)
    return code


if __name__ == "__main__":
    sys.exit(main())
