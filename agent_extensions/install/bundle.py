"""Materialize the AGENT_* instruction bundle from the secret provider (issue #60).

`ae bootstrap --seed <file>` turns a fresh machine into a full-strength agent
setup from one cloud source. It authenticates with the 3-line machine-identity
seed (or the same three variables in the environment), reads the six AGENT_*
keys at the provider's root path, and writes each to its $HOME-relative place,
matching the layout the bundle's own README documents.

Load-bearing properties:
- Idempotent: an unchanged key is never rewritten, and nothing is backed up.
- Backup before edit: a changed file is copied to
  ~/.config/agents/backups/<timestamp>/<relative path> before it is replaced.
- No value leaks: reports and the receipt carry key names, paths and sha16
  hashes only. Seed secrets and bundle values never reach output.
- Degraded, never rule-less: without a seed the stage is skipped and names
  every key it could not materialize; the other bootstrap stages still run.

The bundle's global rules land at ~/AGENTS.md, the same file the baseline shim
manages. The bundle copy carries no managed marker, so the shim stage then
skips it as unmanaged: the owner's live rules always win over the template.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol

from agent_extensions.sync.bootstrap import APPLIED, FAILED, SKIPPED, StageResult

STAGE = "bundle"

SEED_KEYS = ("INFISICAL_CLIENT_ID", "INFISICAL_CLIENT_SECRET", "INFISICAL_PROJECT_ID")

# Key -> path relative to $HOME (the bundle README's source-of-truth layout).
BUNDLE_LAYOUT: dict[str, tuple[str, ...]] = {
    "AGENT_GLOBAL_RULES_MD": ("AGENTS.md",),
    "AGENT_HARNESS_CONFIG_YAML": (".config", "agents", "config.yaml"),
    "AGENT_HARNESS_README_MD": (".config", "agents", "README.md"),
    "AGENT_HARNESS_AGENTS_MD": (".config", "agents", "AGENTS.md"),
    "AGENT_FETCH_AGENT_SECRETS_PS1": (".config", "agents", "fetch-agent-secrets.ps1"),
    "AGENT_MINT_GITHUB_APP_TOKEN_MJS": (".config", "agents", "mint-github-app-token.mjs"),
}

AGENTS_DIR = (".config", "agents")
RECEIPT_NAME = "bundle-materialize-receipt.json"
DEFAULT_API = "https://app.infisical.com"
PROVIDER_ENV = "prod"


class SeedError(ValueError):
    """The machine-identity seed is unreadable or incomplete. Never carries a value."""


class ProviderError(RuntimeError):
    """The secret provider refused or failed a call. Never carries a credential."""


class BundleClient(Protocol):
    """What the stage needs from a secret provider: a login and one root read."""

    def login(self, client_id: str, client_secret: str) -> str: ...

    def fetch_root(self, token: str, project_id: str) -> dict[str, str]: ...


def parse_seed(text: str) -> dict[str, str]:
    """Parse KEY=VALUE seed lines (CRLF, blanks and # comments allowed) into the three identity keys."""
    found: dict[str, str] = {}
    for raw in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        if key.startswith("export "):
            key = key[len("export "):].strip()
        if key in SEED_KEYS:
            found[key] = value.strip().strip('"').strip("'")
    missing = [k for k in SEED_KEYS if not found.get(k)]
    if missing:
        raise SeedError(f"seed is missing {', '.join(missing)}")
    return found


def canonical(value: str) -> str:
    """LF-normalize and end with exactly one newline (the provider CLI trims trailing newlines)."""
    text = value.replace("\r\n", "\n").replace("\r", "\n")
    return text.rstrip("\n") + "\n"


def sha16(data: bytes) -> str:
    """First 16 hex chars of SHA-256: the bundle receipt's fingerprint."""
    return hashlib.sha256(data).hexdigest()[:16]


class InfisicalClient:
    """Minimal stdlib client for universal-auth login and a root-path secret read.

    urllib honors HTTPS_PROXY and the platform CA store, so it works behind a
    corporate or agent proxy. Errors name the HTTP status only.
    """

    def __init__(self, api_base: str = DEFAULT_API, timeout: float = 30.0):
        self.api_base = api_base.rstrip("/")
        self.timeout = timeout

    def _call(self, req: urllib.request.Request) -> dict:
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            raise ProviderError(f"secret provider returned HTTP {exc.code}") from None
        except urllib.error.URLError as exc:
            raise ProviderError(f"secret provider unreachable: {exc.reason}") from None
        except ValueError:
            raise ProviderError("secret provider returned malformed JSON") from None

    def login(self, client_id: str, client_secret: str) -> str:
        body = json.dumps({"clientId": client_id, "clientSecret": client_secret}).encode("utf-8")
        req = urllib.request.Request(
            f"{self.api_base}/api/v1/auth/universal-auth/login",
            data=body,
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        token = self._call(req).get("accessToken")
        if not token:
            raise ProviderError("secret provider login returned no access token")
        return token

    def fetch_root(self, token: str, project_id: str) -> dict[str, str]:
        query = urllib.parse.urlencode({"workspaceId": project_id, "environment": PROVIDER_ENV, "secretPath": "/"})
        req = urllib.request.Request(
            f"{self.api_base}/api/v3/secrets/raw?{query}",
            headers={"Authorization": f"Bearer {token}"},
        )
        rows = self._call(req).get("secrets") or []
        return {
            row["secretKey"]: row.get("secretValue", "")
            for row in rows
            if isinstance(row, dict) and str(row.get("secretKey", "")).startswith("AGENT_")
        }


def _identity(seed_path: Path | None, env: Mapping[str, str]) -> dict[str, str] | None:
    """The seed file wins; otherwise the three environment variables; otherwise None."""
    if seed_path is not None:
        return parse_seed(Path(seed_path).read_text(encoding="utf-8"))
    if all(env.get(k) for k in SEED_KEYS):
        return {k: env[k] for k in SEED_KEYS}
    return None


def _write_private(path: Path, text: str) -> None:
    """Write LF text and make it owner-only on POSIX (bundle files are private machine config)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")
    if os.name != "nt":
        os.chmod(path, 0o600)


def materialize(home: Path, values: Mapping[str, str], *, now: str) -> tuple[list[str], dict[str, dict[str, str]]]:
    """Write each present bundle key; back up a changed file first. Returns notes and receipt entries."""
    notes: list[str] = []
    receipt: dict[str, dict[str, str]] = {}
    backup_root = home.joinpath(*AGENTS_DIR, "backups", now)
    for key, parts in BUNDLE_LAYOUT.items():
        if key not in values:
            continue
        target = home.joinpath(*parts)
        text = canonical(values[key])
        data = text.encode("utf-8")
        if target.exists() and target.read_bytes() == data:
            if os.name != "nt" and (target.stat().st_mode & 0o777) != 0o600:
                os.chmod(target, 0o600)
            notes.append(f"{key}: up to date")
        else:
            if target.exists():
                backup = backup_root.joinpath(*parts)
                backup.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(target, backup)
                notes.append(f"{key}: updated (backup {backup.relative_to(home).as_posix()})")
            else:
                notes.append(f"{key}: installed")
            _write_private(target, text)
        receipt[key] = {"path": "~/" + "/".join(parts), "sha16": sha16(data)}
    return notes, receipt


def stage_bundle(
    home: Path,
    *,
    seed_path: Path | None = None,
    env: Mapping[str, str] | None = None,
    client: BundleClient | None = None,
    now: str | None = None,
) -> StageResult:
    """The bundle stage: applied, skipped (no identity) or failed, never raising, never leaking."""
    environment = os.environ if env is None else env
    stamp = now or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    try:
        identity = _identity(seed_path, environment)
    except (OSError, SeedError) as exc:
        reason = exc if isinstance(exc, SeedError) else f"cannot read seed file ({type(exc).__name__})"
        return StageResult(name=STAGE, status=FAILED, detail=f"{reason}; no bundle key materialized")
    if identity is None:
        return StageResult(
            name=STAGE,
            status=SKIPPED,
            detail="no seed and no INFISICAL_* identity in the environment; skipped "
            + ", ".join(BUNDLE_LAYOUT),
        )

    provider = client or InfisicalClient(environment.get("INFISICAL_API_URL", DEFAULT_API))
    try:
        token = provider.login(identity["INFISICAL_CLIENT_ID"], identity["INFISICAL_CLIENT_SECRET"])
        values = provider.fetch_root(token, identity["INFISICAL_PROJECT_ID"])
    except Exception as exc:  # noqa: BLE001 — report, never raise, never echo a credential
        message = str(exc)
        for secret in identity.values():
            if secret:
                message = message.replace(secret, "[redacted]")
        return StageResult(name=STAGE, status=FAILED, detail=f"{type(exc).__name__}: {message}; no bundle key materialized")

    try:
        notes, receipt = materialize(home, values, now=stamp)
        missing = [k for k in BUNDLE_LAYOUT if k not in values]
        receipt_path = home.joinpath(*AGENTS_DIR, RECEIPT_NAME)
        _write_private(
            receipt_path,
            json.dumps({"materialized_at": stamp, "keys": receipt, "missing": missing}, indent=2, sort_keys=True) + "\n",
        )
    except OSError as exc:
        return StageResult(name=STAGE, status=FAILED, detail=f"cannot write bundle ({type(exc).__name__}: {exc.strerror})")

    if missing:
        return StageResult(
            name=STAGE,
            status=FAILED,
            detail="provider has no " + ", ".join(missing) + "; " + "; ".join(notes),
        )
    return StageResult(name=STAGE, status=APPLIED, detail="; ".join(notes))
