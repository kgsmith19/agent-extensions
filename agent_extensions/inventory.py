"""Environment inventory: which credentials, secret-store entries and CLIs this session has.

Read it first, every session, in every harness (session-start runs it). It answers
"what can I use here?" without a human: credential NAMES from the environment and
from each configured secret store, what each one is for (inventory_catalog.json),
and which CLIs are on PATH.

Rules held here:

- Names only. A store adapter asks the provider to withhold values and drops any it
  sends anyway; nothing here reads, prints, caches or returns a secret value.
- Provider-neutral. Logic sees the `SecretStore` port; Infisical is one adapter,
  chosen by data (its identity variables being present). Providers are named only in
  the catalog and the adapter.
- Degrades, never blocks. An unreachable store is reported as `degraded`, and the
  rest of the inventory still renders.
- Retired paths are listed but flagged, so an agent does not pick them up.

    python3 -m agent_extensions.inventory [--json] [--write] [--summary] [--offline]
    ae inventory ...                       # same
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Mapping, Protocol, Sequence

CATALOG_PATH = Path(__file__).with_name("inventory_catalog.json")
CACHE_DIR = Path.home() / ".agents"
RULES = (
    "Read this before any task. It lists NAMES only; never print, log or copy a value. "
    "Use a secret through `ae secret-run --env VAR=/path:KEY -- <cmd>` (the value lives "
    "only in that command's environment, output is redacted) or fetch it in-process. Never say a "
    "credential or tool is unavailable without checking here (and `ae inventory "
    "--write` to refresh). Retired entries are not for use. Owner-only actions "
    "(new PATs, OAuth consent, billing) stay with the owner."
)


@dataclass(frozen=True)
class Entry:
    source: str  # "env" or "<store>:<environment>"
    path: str
    name: str
    provider: str = "unknown"
    kind: str = "unclassified"
    use: str = ""
    retired: bool = False


@dataclass
class StoreStatus:
    store: str
    state: str  # ok | degraded | skipped
    detail: str = ""
    count: int = 0


@dataclass
class Inventory:
    generated_at: str
    rules: str
    entries: list[Entry] = field(default_factory=list)
    stores: list[StoreStatus] = field(default_factory=list)
    clis: dict[str, bool] = field(default_factory=dict)


class SecretStore(Protocol):
    """Port: a secret store that lists secret NAMES and hands one value to `secret_run` only."""

    label: str

    def list_names(self) -> list[tuple[str, str]]: ...

    def get(self, path: str, name: str) -> str: ...


class InfisicalStore:
    """Adapter: Infisical universal-auth identity from the environment, recursive names-only list."""

    def __init__(self, env: Mapping[str, str], client: Any = None, environment: str = "prod") -> None:
        from agent_extensions.install.bundle import DEFAULT_API, InfisicalClient

        self._env = env
        self._environment = environment
        self._client = client or InfisicalClient(env.get("INFISICAL_API_URL", DEFAULT_API))
        self.label = f"infisical:{environment}"

    @staticmethod
    def configured(env: Mapping[str, str]) -> bool:
        return all(env.get(k) for k in ("INFISICAL_CLIENT_ID", "INFISICAL_CLIENT_SECRET", "INFISICAL_PROJECT_ID"))

    def list_names(self) -> list[tuple[str, str]]:
        token = self._client.login(self._env["INFISICAL_CLIENT_ID"], self._env["INFISICAL_CLIENT_SECRET"])
        rows = self._client._list(  # noqa: SLF001 - same package, names-only query
            token,
            self._env["INFISICAL_PROJECT_ID"],
            "/",
            recursive="true",
            viewSecretValue="false",
        )
        return sorted(
            (str(row.get("secretPath") or "/"), str(row["secretKey"])) for row in rows if row.get("secretKey")
        )

    def get(self, path: str, name: str) -> str:
        """One value, for `secret_run` to place in a child's environment. Never printed."""
        token = self._client.login(self._env["INFISICAL_CLIENT_ID"], self._env["INFISICAL_CLIENT_SECRET"])
        rows = self._client._list(token, self._env["INFISICAL_PROJECT_ID"], path)  # noqa: SLF001
        for row in rows:
            if row.get("secretKey") == name:
                return str(row.get("secretValue") or "")
        raise LookupError(f"{name} not found at {path}")


def load_catalog(path: Path = CATALOG_PATH) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def classify(source: str, path: str, name: str, catalog: Mapping[str, Any]) -> Entry:
    retired = any(path.startswith(p) for p in catalog.get("retired_path_prefixes", []))
    for rule in catalog.get("credentials", []):
        if re.search(rule["match"], name):
            return Entry(source, path, name, rule["provider"], rule["kind"], rule["use"], retired)
    return Entry(source, path, name, retired=retired)


def env_names(env: Mapping[str, str], catalog: Mapping[str, Any]) -> list[str]:
    pattern = re.compile(catalog["env_name_pattern"])
    ignore = [re.compile(p) for p in catalog.get("env_name_ignore", [])]
    return sorted(
        name
        for name, value in env.items()
        if value and pattern.search(name) and not any(i.search(name) for i in ignore)
    )


def default_stores(env: Mapping[str, str]) -> list[SecretStore]:
    stores: list[SecretStore] = []
    if InfisicalStore.configured(env):
        stores.append(InfisicalStore(env))
    return stores


def build(
    env: Mapping[str, str],
    stores: Sequence[SecretStore],
    catalog: Mapping[str, Any],
    which: Any = shutil.which,
    now: datetime | None = None,
) -> Inventory:
    inv = Inventory(
        generated_at=(now or datetime.now(UTC)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        rules=RULES,
    )
    inv.entries.extend(classify("env", "$ENV", name, catalog) for name in env_names(env, catalog))
    for store in stores:
        try:
            names = store.list_names()
        except Exception as error:  # a store outage degrades the inventory, never blocks
            inv.stores.append(StoreStatus(store.label, "degraded", type(error).__name__ + _status(error)))
            continue
        inv.entries.extend(classify(store.label, p, n, catalog) for p, n in names)
        inv.stores.append(StoreStatus(store.label, "ok", count=len(names)))
    if not stores:
        inv.stores.append(StoreStatus("secret-store", "skipped", "no store identity in the environment"))
    inv.clis = {cli: which(cli) is not None for cli in catalog.get("clis", [])}
    return inv


def _status(error: Exception) -> str:
    match = re.search(r"HTTP \d{3}", str(error))
    return f" ({match.group(0)})" if match else ""


def render_markdown(inv: Inventory) -> str:
    live = [e for e in inv.entries if not e.retired]
    retired = [e for e in inv.entries if e.retired]
    out = [
        "# Environment inventory (names only)",
        "",
        f"Generated {inv.generated_at}. {inv.rules}",
        "",
        "## Secret stores",
        "",
    ]
    out += [f"- `{s.store}`: {s.state}" + (f" ({s.count} names)" if s.count else "") + (f" - {s.detail}" if s.detail else "") for s in inv.stores]
    out += ["", "## Credentials", "", "| Source | Path | Name | Provider | Kind | Use |", "| --- | --- | --- | --- | --- | --- |"]
    out += [f"| {e.source} | `{e.path}` | `{e.name}` | {e.provider} | {e.kind} | {e.use} |" for e in live]
    if retired:
        out += ["", f"Retired (do not use): " + ", ".join(f"`{e.path}/{e.name}`" for e in retired)]
    have = sorted(c for c, ok in inv.clis.items() if ok)
    missing = sorted(c for c, ok in inv.clis.items() if not ok)
    out += ["", "## CLIs", "", "On PATH: " + (", ".join(have) or "none"), "", "Missing: " + (", ".join(missing) or "none"), ""]
    return "\n".join(out)


def summary(inv: Inventory, md_path: Path | None) -> str:
    live = [e for e in inv.entries if not e.retired]
    providers = sorted({e.provider for e in live if e.provider != "unknown"})
    stores = ", ".join(f"{s.store} {s.state}" for s in inv.stores)
    where = f"; read {md_path}" if md_path else ""
    return (
        f"inventory: ok ({len(live)} credential names across env + stores [{stores}]; "
        f"providers: {', '.join(providers) or 'none'}; "
        f"CLIs: {sum(inv.clis.values())}/{len(inv.clis)} on PATH{where})"
    )


def write_cache(inv: Inventory, directory: Path = CACHE_DIR) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    md = directory / "inventory.md"
    for path, text in ((md, render_markdown(inv)), (directory / "inventory.json", json.dumps(asdict(inv), indent=2))):
        path.write_text(text, encoding="utf-8")
        os.chmod(path, 0o600)
    return md


def main(argv: Sequence[str] | None = None, env: Mapping[str, str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="ae inventory", description=__doc__.split("\n\n")[0])
    parser.add_argument("--json", action="store_true", help="print JSON instead of markdown")
    parser.add_argument("--write", action="store_true", help="also write ~/.agents/inventory.{md,json} (0600)")
    parser.add_argument("--summary", action="store_true", help="print one line (session start)")
    parser.add_argument("--offline", action="store_true", help="skip secret stores; env and CLIs only")
    args = parser.parse_args(argv)
    environment = env if env is not None else os.environ
    catalog = load_catalog()
    inv = build(environment, [] if args.offline else default_stores(environment), catalog)
    md_path = write_cache(inv) if args.write else None
    if args.summary:
        print(summary(inv, md_path))
    elif args.json:
        print(json.dumps(asdict(inv), indent=2))
    else:
        print(render_markdown(inv))
    return 0


if __name__ == "__main__":
    sys.exit(main())
