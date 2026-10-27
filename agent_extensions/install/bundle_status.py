"""Verify the materialized AGENT_* bundle against its source (issue #61).

`ae status` runs this as a drift and freshness gate at session start. Each
bundle key's local file is hashed in canonical form and compared with:
- the secret provider's current value, when a machine identity is available
  (seed file or the INFISICAL_* environment); or
- the last bootstrap receipt, when it is not (a local edit is still caught,
  cloud freshness is reported as not checked).

Exit contract (harnesses may gate on it):
  0  every key MATCHes, or there is nothing to verify against
  1  any key is DRIFT or MISSING; the converge command is printed
  2  the provider cannot be reached or the seed is unreadable

Read-only: one login, one root read and one key-name listing. Provider values
are hashed in memory and dropped; nothing is written, and no value, token or
seed secret is printed. `ae bootstrap` stays the only writer (`--repair` runs
its bundle stage).
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path

from agent_extensions.install.bundle import (
    AGENTS_DIR,
    BUNDLE_LAYOUT,
    DEFAULT_API,
    RECEIPT_NAME,
    InfisicalClient,
    SeedError,
    _identity,
    canonical,
)

MATCH, DRIFT, MISSING = "MATCH", "DRIFT", "MISSING"
EXIT_OK, EXIT_DRIFT, EXIT_UNREACHABLE = 0, 1, 2

# Dev-agent GitHub App credentials (hyperbolic-core dev-agent-dispatch.yml reads them here).
DEV_APP_PATH = "/dev/"
DEV_APP_KEYS = ("DEV_GITHUB_APP_ID", "DEV_GITHUB_APP_PRIVATE_KEY")
MINT_KEY = "AGENT_MINT_GITHUB_APP_TOKEN_MJS"


@dataclass
class KeyRow:
    key: str
    path: str
    state: str
    local: str | None = None  # sha16 of the local file's canonical form
    expected: str | None = None  # sha16 from the provider or the receipt
    note: str = ""


@dataclass
class BundleStatus:
    source: str  # "provider" | "receipt" | "none"
    provider: str
    rows: list[KeyRow] = field(default_factory=list)
    mint: str = "not checked"
    exit_code: int = EXIT_OK
    converge: str = "ae bootstrap"

    def lines(self) -> list[str]:
        out = [f"{'failed' if self.exit_code == EXIT_UNREACHABLE else 'ok':8} bundle/provider: {self.provider}"]
        reference = "cloud" if self.source == "provider" else "receipt"
        for row in self.rows:
            note = f"; {row.note}" if row.note else ""
            out.append(
                f"{row.state:8} bundle/{row.key}: {row.path} "
                f"(local {row.local or '-'}, {reference} {row.expected or '-'}{note})"
            )
        out.append(f"{'info':8} bundle/mint: {self.mint}")
        if self.exit_code != EXIT_OK:
            out.append(f"converge: {self.converge}")
        return out


def default_client(env: Mapping[str, str]) -> InfisicalClient:
    return InfisicalClient(env.get("INFISICAL_API_URL", DEFAULT_API))


def _canonical_sha(text: str) -> str:
    return hashlib.sha256(canonical(text).encode("utf-8")).hexdigest()[:16]


def _local_sha(target: Path) -> str | None:
    """sha16 of the file's canonical form; an undecodable file gets a raw-bytes hash (never a match)."""
    if not target.is_file():
        return None
    data = target.read_bytes()
    try:
        return _canonical_sha(data.decode("utf-8"))
    except UnicodeDecodeError:
        return "raw:" + hashlib.sha256(data).hexdigest()[:12]


def _row(home: Path, key: str, expected: str | None, note_if_absent: str) -> KeyRow:
    parts = BUNDLE_LAYOUT[key]
    path = "~/" + "/".join(parts)
    local = _local_sha(home.joinpath(*parts))
    if expected is None:
        return KeyRow(key, path, MISSING, local, None, note_if_absent)
    if local is None:
        return KeyRow(key, path, MISSING, None, expected, "no local file")
    return KeyRow(key, path, MATCH if local == expected else DRIFT, local, expected)


def _redact(message: str, identity: Mapping[str, str]) -> str:
    for secret in identity.values():
        if secret:
            message = message.replace(secret, "[redacted]")
    return message


def _mint(home: Path, client, token: str, project_id: str, which: Callable[[str], str | None]) -> str:
    """Can this machine mint a dev-agent App token? Checks the pieces; never mints, never reads a value."""
    missing = []
    if not home.joinpath(*BUNDLE_LAYOUT[MINT_KEY]).is_file():
        missing.append("~/" + "/".join(BUNDLE_LAYOUT[MINT_KEY]))
    if which("node") is None:
        missing.append("node")
    try:
        keys = client.list_keys(token, project_id, DEV_APP_PATH)
    except Exception as exc:  # noqa: BLE001 — informational line, never fails status
        return f"unknown (cannot list {DEV_APP_PATH}: {type(exc).__name__}: {exc})"
    missing += [f"{k} at {DEV_APP_PATH}" for k in DEV_APP_KEYS if k not in keys]
    if missing:
        return "unavailable (missing: " + ", ".join(missing) + ")"
    return f"ready (mint script, node, {' + '.join(DEV_APP_KEYS)} provisioned at {DEV_APP_PATH}; no token minted)"


def _exit_for(rows: list[KeyRow]) -> int:
    return EXIT_DRIFT if any(r.state != MATCH for r in rows) else EXIT_OK


def verify(
    home: Path,
    *,
    seed_path: Path | None = None,
    env: Mapping[str, str] | None = None,
    client=None,
    which: Callable[[str], str | None] = shutil.which,
) -> BundleStatus:
    """Compare every bundle key's local file with its source. Never writes, never raises."""
    environment = os.environ if env is None else env
    converge = f"ae bootstrap --seed {seed_path}" if seed_path is not None else "ae bootstrap"
    try:
        identity = _identity(seed_path, environment)
    except (OSError, SeedError) as exc:
        reason = exc if isinstance(exc, SeedError) else f"cannot read seed file ({type(exc).__name__})"
        return BundleStatus("none", f"not checked ({reason})", exit_code=EXIT_UNREACHABLE, converge=converge)

    if identity is None:
        receipt_path = home.joinpath(*AGENTS_DIR, RECEIPT_NAME)
        try:
            receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
            recorded = {k: v.get("sha16") for k, v in (receipt.get("keys") or {}).items() if isinstance(v, dict)}
        except (OSError, ValueError, AttributeError):
            return BundleStatus(
                "none",
                "not verified (no seed, no INFISICAL_* identity and no bootstrap receipt); "
                "cloud freshness not checked",
            )
        rows = [_row(home, key, recorded.get(key), "not in the bootstrap receipt") for key in BUNDLE_LAYOUT]
        return BundleStatus(
            "receipt",
            "not checked (no seed or INFISICAL_* identity); compared with the last bootstrap receipt",
            rows,
            mint="not checked (no identity)",
            exit_code=_exit_for(rows),
            converge=converge,
        )

    provider = client or default_client(environment)
    try:
        token, ttl = provider.login_with_ttl(identity["INFISICAL_CLIENT_ID"], identity["INFISICAL_CLIENT_SECRET"])
        values = provider.fetch_root(token, identity["INFISICAL_PROJECT_ID"])
    except Exception as exc:  # noqa: BLE001 — report, never raise, never echo a credential
        message = _redact(f"{type(exc).__name__}: {exc}", identity)
        return BundleStatus(
            "provider",
            f"unreachable ({message}); no key verified",
            mint="not checked (provider unreachable)",
            exit_code=EXIT_UNREACHABLE,
            converge=converge,
        )
    # Hash in memory; the values themselves go no further.
    cloud = {key: _canonical_sha(value) for key, value in values.items() if key in BUNDLE_LAYOUT}
    rows = [_row(home, key, cloud.get(key), f"provider has no {key}") for key in BUNDLE_LAYOUT]
    mint = _redact(_mint(home, provider, token, identity["INFISICAL_PROJECT_ID"], which), identity)
    api = environment.get("INFISICAL_API_URL", DEFAULT_API)
    ttl_text = f"token TTL {ttl}s" if ttl is not None else "token TTL not reported"
    return BundleStatus(
        "provider",
        f"reachable ({api}), {ttl_text}",
        rows,
        mint=mint,
        exit_code=_exit_for(rows),
        converge=converge,
    )
