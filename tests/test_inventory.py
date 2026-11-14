"""Environment inventory and secret-run: names only, provider-neutral, degrade not block."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

from agent_extensions import inventory, secret_run

CANARY = "LEAKCANARY-9f3c"
CATALOG = inventory.load_catalog()


class FakeStore:
    label = "fake:prod"

    def __init__(self, names=None, fail=False, values=None):
        self.names = names or [("/", "CLOUDFLARE_API_TOKEN"), ("/retired/x", "OLD_TOKEN"), ("/dev", "MYSTERY")]
        self.fail = fail
        self.values = values or {("/", "CLOUDFLARE_API_TOKEN"): CANARY}

    def list_names(self):
        if self.fail:
            raise RuntimeError("secret provider returned HTTP 503")
        return self.names

    def get(self, path, name):
        try:
            return self.values[(path, name)]
        except KeyError:
            raise LookupError(f"{name} not found at {path}") from None


ENV = {"GH_TOKEN": CANARY, "INFISICAL_CLIENT_ID": CANARY, "MAX_THINKING_TOKENS": "1", "HOME": "/x", "EMPTY_TOKEN": ""}


def test_env_names_only_credentials_minus_ignores_and_empties():
    assert inventory.env_names(ENV, CATALOG) == ["GH_TOKEN", "INFISICAL_CLIENT_ID"]


def test_build_classifies_flags_retired_and_never_holds_a_value():
    inv = inventory.build(ENV, [FakeStore()], CATALOG, which=lambda c: "/bin/" + c if c == "git" else None)
    by_name = {e.name: e for e in inv.entries}
    assert by_name["CLOUDFLARE_API_TOKEN"].provider == "cloudflare"
    assert by_name["OLD_TOKEN"].retired is True
    assert by_name["MYSTERY"].kind == "unclassified"
    assert inv.stores[0].state == "ok" and inv.stores[0].count == 3
    assert inv.clis["git"] is True and inv.clis["gh"] is False
    rendered = inventory.render_markdown(inv) + json.dumps(inventory.asdict(inv))
    assert CANARY not in rendered
    assert "Retired (do not use): `/retired/x/OLD_TOKEN`" in rendered


def test_store_outage_degrades_and_env_still_reported():
    inv = inventory.build(ENV, [FakeStore(fail=True)], CATALOG, which=lambda c: None)
    assert inv.stores[0].state == "degraded" and "HTTP 503" in inv.stores[0].detail
    assert {e.name for e in inv.entries} == {"GH_TOKEN", "INFISICAL_CLIENT_ID"}


def test_no_store_identity_is_skipped_not_an_error():
    inv = inventory.build({}, [], CATALOG, which=lambda c: None)
    assert inv.stores[0].state == "skipped"


def test_write_cache_is_owner_only(tmp_path: Path):
    md = inventory.write_cache(inventory.build(ENV, [FakeStore()], CATALOG, which=lambda c: None), tmp_path)
    assert md.read_text().startswith("# Environment inventory (names only)")
    assert oct(md.stat().st_mode & 0o777) == "0o600"
    assert oct((tmp_path / "inventory.json").stat().st_mode & 0o777) == "0o600"


def test_infisical_adapter_is_chosen_by_data_and_asks_for_names_only():
    calls = []

    class Client:
        def login(self, cid, secret):
            return "tok"

        def _list(self, token, project, path, **extra):
            calls.append(extra)
            return [{"secretKey": "A", "secretPath": "/p", "secretValue": CANARY}]

    env = {"INFISICAL_CLIENT_ID": "i", "INFISICAL_CLIENT_SECRET": "s", "INFISICAL_PROJECT_ID": "p"}
    assert inventory.InfisicalStore.configured(env) and not inventory.InfisicalStore.configured({})
    names = inventory.InfisicalStore(env, client=Client()).list_names()
    assert names == [("/p", "A")]
    assert calls == [{"recursive": "true", "viewSecretValue": "false"}]


def test_catalog_holds_no_value_shaped_strings():
    text = inventory.CATALOG_PATH.read_text()
    for shape in (r"sk-[A-Za-z0-9]{8,}", r"ghp_[A-Za-z0-9]{8,}", r"AKIA[0-9A-Z]{16}"):
        assert not __import__("re").search(shape, text)


def test_secret_run_injects_into_child_only_and_redacts_output():
    code, out, err = secret_run.run(
        ["CF=/:CLOUDFLARE_API_TOKEN"],
        [sys.executable, "-c", "import os,sys; print('got', os.environ['CF']); print(os.environ['CF'], file=sys.stderr)"],
        FakeStore(),
        {"PATH": "/usr/bin"},
    )
    assert code == 0
    assert out.strip() == "got [REDACTED:CF]" and err.strip() == "[REDACTED:CF]"
    assert CANARY not in out + err


def test_secret_run_refuses_bad_specs_and_missing_secrets():
    with pytest.raises(ValueError):
        secret_run.parse_specs(["lowercase=/:X"])
    with pytest.raises(LookupError):
        secret_run.run(["X=/:NOPE"], ["true"], FakeStore(), {})


def test_cli_offline_summary_runs_without_network(tmp_path: Path):
    done = subprocess.run(
        [sys.executable, "-m", "agent_extensions.inventory", "--offline", "--summary"],
        capture_output=True,
        text=True,
        env={"PATH": "/usr/bin:/bin", "HOME": str(tmp_path), "PYTHONPATH": str(Path(__file__).resolve().parents[1])},
        check=True,
    )
    assert done.stdout.startswith("inventory: ok (")
