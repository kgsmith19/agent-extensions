"""`ae status` verifies the materialized AGENT_* bundle (issue #61).

Drift and freshness gate: each bundle key's local file is hashed in canonical
form and compared with the provider's value (or, without an identity, with the
last bootstrap receipt). The provider client is faked. These tests pin the
per-key MATCH / DRIFT / MISSING table, the exit-code contract harnesses gate
on (0 ok, 1 drift or missing, 2 provider unreachable), the read-only
guarantee, the mint-capability line, and that no value or token is printed.
"""
import hashlib
import io
import json
import urllib.request
from pathlib import Path

import pytest

SEED = "INFISICAL_CLIENT_ID=cid-123\nINFISICAL_CLIENT_SECRET=sec-ret-value\nINFISICAL_PROJECT_ID=proj-9\n"

VALUES = {
    "AGENT_GLOBAL_RULES_MD": "# Global rules\nLEAKCANARY-RULES",
    "AGENT_HARNESS_CONFIG_YAML": "providers:\n  secret: LEAKCANARY-CONFIG",
    "AGENT_HARNESS_README_MD": "# Harness README\nLEAKCANARY-README",
    "AGENT_HARNESS_AGENTS_MD": "# Harness dir\nLEAKCANARY-DIR",
    "AGENT_FETCH_AGENT_SECRETS_PS1": "Write-Output 'LEAKCANARY-PS1'",
    "AGENT_MINT_GITHUB_APP_TOKEN_MJS": "console.log('LEAKCANARY-MJS')",
}

TOKEN = "TOKENCANARY-abc"
DEV_KEYS = {"DEV_GITHUB_APP_ID", "DEV_GITHUB_APP_PRIVATE_KEY", "DEV_OPENAI_API_KEY"}


class FakeClient:
    """Read-only fake provider; records every call so a write would be visible."""

    def __init__(self, values=None, error=None, dev_keys=None, list_error=None, ttl=3600):
        self.values = dict(VALUES if values is None else values)
        self.error = error
        self.dev_keys = set(DEV_KEYS if dev_keys is None else dev_keys)
        self.list_error = list_error
        self.ttl = ttl
        self.calls = []

    def login_with_ttl(self, client_id, client_secret):
        self.calls.append("login_with_ttl")
        if self.error:
            raise self.error
        return TOKEN, self.ttl

    def login(self, client_id, client_secret):
        self.calls.append("login")
        if self.error:
            raise self.error
        return TOKEN

    def fetch_root(self, token, project_id):
        self.calls.append("fetch_root")
        return dict(self.values)

    def list_keys(self, token, project_id, path):
        self.calls.append(("list_keys", path))
        if self.list_error:
            raise self.list_error
        return set(self.dev_keys)


@pytest.fixture()
def home(tmp_path, monkeypatch):
    h = tmp_path / "home"
    h.mkdir()
    monkeypatch.setenv("HOME", str(h))
    monkeypatch.setenv("USERPROFILE", str(h))
    return h


@pytest.fixture()
def seed(tmp_path):
    p = tmp_path / "seed.env"
    p.write_text(SEED, encoding="utf-8")
    return p


def _status():
    from agent_extensions.install import bundle_status

    return bundle_status


def _materialize(home, seed, values=None):
    from agent_extensions.install import bundle

    result = bundle.stage_bundle(home, seed_path=seed, env={}, client=FakeClient(values=values), now="20261027T070000Z")
    assert result.status == "applied", result.detail
    return result


def _verify(home, seed=None, client=None, env=None, node=True):
    s = _status()
    c = client if client is not None else FakeClient()
    which = (lambda name: "/usr/bin/node" if name == "node" else None) if node else (lambda name: None)
    return s.verify(home, seed_path=seed, env=env if env is not None else {}, client=c, which=which), c


def _rows(report):
    return {row.key: row.state for row in report.rows}


def _text(report):
    return "\n".join(report.lines())


def _snapshot(root: Path):
    return {p.relative_to(root).as_posix(): (p.stat().st_mtime_ns, p.read_bytes()) for p in sorted(root.rglob("*")) if p.is_file()}


# --- AC1: per-key MATCH / DRIFT / MISSING -----------------------------------

def test_freshly_materialized_bundle_matches_every_key_and_exits_0(home, seed):
    _materialize(home, seed)
    report, _ = _verify(home, seed)
    assert _rows(report) == {k: "MATCH" for k in VALUES}
    assert report.exit_code == 0
    assert report.source == "provider"


def test_each_row_reports_local_and_cloud_sha16_of_the_canonical_form(home, seed):
    _materialize(home, seed)
    report, _ = _verify(home, seed)
    want = hashlib.sha256((VALUES["AGENT_GLOBAL_RULES_MD"] + "\n").encode()).hexdigest()[:16]
    row = next(r for r in report.rows if r.key == "AGENT_GLOBAL_RULES_MD")
    assert row.local == want and row.expected == want
    assert row.path == "~/AGENTS.md"


def test_a_locally_edited_file_is_drift_and_exits_1(home, seed):
    _materialize(home, seed)
    (home / "AGENTS.md").write_text("# Global rules\nedited locally\n", encoding="utf-8")
    report, _ = _verify(home, seed)
    assert _rows(report)["AGENT_GLOBAL_RULES_MD"] == "DRIFT"
    assert [k for k, v in _rows(report).items() if v != "MATCH"] == ["AGENT_GLOBAL_RULES_MD"]
    assert report.exit_code == 1


def test_a_changed_cloud_value_makes_the_local_file_stale_drift(home, seed):
    _materialize(home, seed)
    newer = dict(VALUES, AGENT_HARNESS_CONFIG_YAML="providers:\n  secret: NEWER")
    report, _ = _verify(home, seed, client=FakeClient(values=newer))
    assert _rows(report)["AGENT_HARNESS_CONFIG_YAML"] == "DRIFT"
    assert report.exit_code == 1


def test_a_missing_local_file_is_missing_and_exits_1(home, seed):
    _materialize(home, seed)
    (home / ".config" / "agents" / "README.md").unlink()
    report, _ = _verify(home, seed)
    assert _rows(report)["AGENT_HARNESS_README_MD"] == "MISSING"
    assert report.exit_code == 1


def test_a_key_the_provider_lacks_is_missing_and_exits_1(home, seed):
    _materialize(home, seed)
    partial = {k: v for k, v in VALUES.items() if k != "AGENT_HARNESS_AGENTS_MD"}
    report, _ = _verify(home, seed, client=FakeClient(values=partial))
    assert _rows(report)["AGENT_HARNESS_AGENTS_MD"] == "MISSING"
    assert "provider has no AGENT_HARNESS_AGENTS_MD" in _text(report)
    assert report.exit_code == 1


def test_comparison_uses_canonical_form_so_crlf_and_trailing_newlines_match(home, seed):
    _materialize(home, seed)
    (home / "AGENTS.md").write_bytes(b"# Global rules\r\nLEAKCANARY-RULES\r\n\r\n")
    report, _ = _verify(home, seed)
    assert _rows(report)["AGENT_GLOBAL_RULES_MD"] == "MATCH"
    assert report.exit_code == 0


def test_an_undecodable_local_file_is_drift_not_a_crash(home, seed):
    _materialize(home, seed)
    (home / "AGENTS.md").write_bytes(b"\xff\xfe\x00binary")
    report, _ = _verify(home, seed)
    assert _rows(report)["AGENT_GLOBAL_RULES_MD"] == "DRIFT"
    assert report.exit_code == 1


# --- AC2: provider reachability, token TTL, mint capability -----------------

def test_reachable_provider_reports_the_token_ttl_never_the_token(home, seed):
    _materialize(home, seed)
    report, _ = _verify(home, seed, client=FakeClient(ttl=7200))
    text = _text(report)
    assert "reachable" in text and "token TTL 7200s" in text
    assert TOKEN not in text


def test_unreachable_provider_exits_2_redacts_and_still_prints_the_converge_command(home, seed):
    from agent_extensions.install.bundle import ProviderError

    _materialize(home, seed)
    err = ProviderError("login failed for sec-ret-value: HTTP 401")
    report, _ = _verify(home, seed, client=FakeClient(error=err))
    text = _text(report)
    assert report.exit_code == 2
    assert "unreachable" in text and "HTTP 401" in text
    assert "sec-ret-value" not in text
    assert f"ae bootstrap --seed {seed}" in text


def test_an_unreadable_seed_exits_2_without_echoing_it(home, tmp_path):
    bad = tmp_path / "bad.env"
    bad.write_text("INFISICAL_CLIENT_ID=only-this\n", encoding="utf-8")
    report, client = _verify(home, bad)
    assert report.exit_code == 2
    assert "INFISICAL_CLIENT_SECRET" in _text(report) and "only-this" not in _text(report)
    assert client.calls == []


def test_mint_is_ready_when_script_node_and_dev_app_keys_are_all_present(home, seed):
    _materialize(home, seed)
    report, client = _verify(home, seed)
    assert ("list_keys", "/dev/") in client.calls
    assert report.mint.startswith("ready")


def test_mint_names_each_missing_piece_and_never_changes_the_exit_code(home, seed):
    _materialize(home, seed)
    (home / ".config" / "agents" / "mint-github-app-token.mjs").unlink()
    keys = {"DEV_GITHUB_APP_ID"}
    report, _ = _verify(home, seed, client=FakeClient(dev_keys=keys), node=False)
    assert report.mint.startswith("unavailable")
    for piece in ("mint-github-app-token.mjs", "node", "DEV_GITHUB_APP_PRIVATE_KEY"):
        assert piece in report.mint
    assert "DEV_GITHUB_APP_ID" not in report.mint.split("missing", 1)[1]
    # The script's absence is already MISSING in the table; the mint line is informational.
    assert report.exit_code == 1
    assert _rows(report)["AGENT_MINT_GITHUB_APP_TOKEN_MJS"] == "MISSING"


def test_mint_alone_never_fails_status(home, seed):
    _materialize(home, seed)
    report, _ = _verify(home, seed, client=FakeClient(dev_keys=set()), node=False)
    assert report.mint.startswith("unavailable")
    assert report.exit_code == 0


def test_a_dev_path_listing_error_is_mint_unknown_not_a_failure(home, seed):
    from agent_extensions.install.bundle import ProviderError

    _materialize(home, seed)
    report, _ = _verify(home, seed, client=FakeClient(list_error=ProviderError("secret provider returned HTTP 403")))
    assert report.mint.startswith("unknown") and "HTTP 403" in report.mint
    assert report.exit_code == 0


# --- AC3: exit contract and the converge command -----------------------------

def test_drift_prints_the_exact_bootstrap_command_and_match_does_not(home, seed):
    _materialize(home, seed)
    clean, _ = _verify(home, seed)
    assert "converge:" not in _text(clean)
    (home / "AGENTS.md").write_text("changed\n", encoding="utf-8")
    drifted, _ = _verify(home, seed)
    assert f"converge: ae bootstrap --seed {seed}" in _text(drifted)


def test_env_identity_converges_with_plain_bootstrap(home, seed):
    _materialize(home, seed)
    (home / "AGENTS.md").write_text("changed\n", encoding="utf-8")
    env = {"INFISICAL_CLIENT_ID": "cid", "INFISICAL_CLIENT_SECRET": "sec-ret-value", "INFISICAL_PROJECT_ID": "proj"}
    report, _ = _verify(home, None, env=env)
    assert report.source == "provider"
    assert "converge: ae bootstrap" in _text(report)
    assert "--seed" not in _text(report)


def test_without_identity_the_receipt_still_catches_a_local_edit(home, seed):
    _materialize(home, seed)
    (home / ".config" / "agents" / "config.yaml").write_text("tampered\n", encoding="utf-8")
    report, client = _verify(home, None)
    assert report.source == "receipt"
    assert client.calls == []
    assert _rows(report)["AGENT_HARNESS_CONFIG_YAML"] == "DRIFT"
    assert "not checked" in _text(report)
    assert report.exit_code == 1


def test_without_identity_or_receipt_status_is_not_verified_and_exits_0(home):
    report, client = _verify(home, None)
    assert report.source == "none"
    assert client.calls == []
    assert report.rows == []
    assert "not verified" in _text(report)
    assert report.exit_code == 0


# --- AC4: read-only and cheap; no value or token ever printed ----------------

def test_status_never_writes_anything(home, seed):
    _materialize(home, seed)
    (home / "AGENTS.md").write_text("changed\n", encoding="utf-8")
    before = _snapshot(home)
    _verify(home, seed)
    assert _snapshot(home) == before


def test_status_makes_only_read_calls(home, seed):
    _materialize(home, seed)
    _, client = _verify(home, seed)
    assert client.calls == ["login_with_ttl", "fetch_root", ("list_keys", "/dev/")]


def test_no_bundle_value_token_or_seed_secret_appears_in_output(home, seed):
    _materialize(home, seed)
    (home / "AGENTS.md").write_text("changed\n", encoding="utf-8")
    text = _text(_verify(home, seed)[0])
    assert "LEAKCANARY" not in text and TOKEN not in text and "sec-ret-value" not in text


# --- CLI ---------------------------------------------------------------------

def _cli(monkeypatch, argv, client):
    from agent_extensions.install import __main__ as cli
    from agent_extensions.install import bundle_status

    repo = Path(__file__).resolve().parents[1]
    monkeypatch.setenv("AGENT_EXTENSIONS_DIR", str(repo))
    monkeypatch.setattr(bundle_status, "default_client", lambda env: client)
    return cli.main(argv)


def test_cli_status_exit_code_follows_the_bundle_gate(monkeypatch, home, seed, capsys):
    _materialize(home, seed)
    assert _cli(monkeypatch, ["status", "--seed", str(seed)], FakeClient()) == 0
    out = capsys.readouterr().out
    assert "gitignore" in out and "bundle/AGENT_GLOBAL_RULES_MD" in out
    (home / "AGENTS.md").write_text("changed\n", encoding="utf-8")
    assert _cli(monkeypatch, ["status", "--seed", str(seed)], FakeClient()) == 1
    from agent_extensions.install.bundle import ProviderError

    assert _cli(monkeypatch, ["status", "--seed", str(seed)], FakeClient(error=ProviderError("HTTP 503"))) == 2


def test_cli_status_without_identity_keeps_exit_0(monkeypatch, home, capsys):
    assert _cli(monkeypatch, ["status"], FakeClient()) == 0
    assert "not verified" in capsys.readouterr().out


def test_repair_is_opt_in_and_converges_through_the_bootstrap_bundle_stage(monkeypatch, home, seed, capsys):
    _materialize(home, seed)
    (home / "AGENTS.md").write_text("changed\n", encoding="utf-8")
    assert _cli(monkeypatch, ["status", "--seed", str(seed)], FakeClient()) == 1
    assert (home / "AGENTS.md").read_text(encoding="utf-8") == "changed\n"
    capsys.readouterr()
    assert _cli(monkeypatch, ["status", "--seed", str(seed), "--repair"], FakeClient()) == 0
    out = capsys.readouterr().out
    assert "repair:" in out and "bundle" in out
    assert (home / "AGENTS.md").read_text(encoding="utf-8") == VALUES["AGENT_GLOBAL_RULES_MD"] + "\n"
    assert list((home / ".config" / "agents" / "backups").rglob("AGENTS.md"))


def test_repair_does_nothing_when_the_provider_is_unreachable(monkeypatch, home, seed, capsys):
    from agent_extensions.install.bundle import ProviderError

    _materialize(home, seed)
    (home / "AGENTS.md").write_text("changed\n", encoding="utf-8")
    rc = _cli(monkeypatch, ["status", "--seed", str(seed), "--repair"], FakeClient(error=ProviderError("HTTP 503")))
    assert rc == 2
    assert (home / "AGENTS.md").read_text(encoding="utf-8") == "changed\n"


# --- the real client's read-only calls ---------------------------------------

def test_real_client_reports_ttl_and_lists_key_names_only(monkeypatch):
    from agent_extensions.install.bundle import InfisicalClient

    seen = []

    class Resp(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def fake_urlopen(req, timeout=None):
        seen.append(req)
        if req.full_url.endswith("/login"):
            return Resp(json.dumps({"accessToken": TOKEN, "expiresIn": 5400}).encode())
        rows = [{"secretKey": "DEV_GITHUB_APP_ID", "secretValue": "VALUECANARY"}, {"secretKey": "DEV_GITHUB_APP_PRIVATE_KEY"}]
        return Resp(json.dumps({"secrets": rows}).encode())

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    c = InfisicalClient("https://example.test")
    assert c.login_with_ttl("cid", "sec") == (TOKEN, 5400)
    assert c.login("cid", "sec") == TOKEN
    keys = c.list_keys(TOKEN, "proj-9", "/dev/")
    assert keys == {"DEV_GITHUB_APP_ID", "DEV_GITHUB_APP_PRIVATE_KEY"}
    listing = seen[-1]
    assert listing.get_method() == "GET"
    assert "secretPath=%2Fdev%2F" in listing.full_url and "viewSecretValue=false" in listing.full_url


def test_real_client_ttl_is_none_when_the_provider_omits_it(monkeypatch):
    from agent_extensions.install.bundle import InfisicalClient

    class Resp(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(urllib.request, "urlopen", lambda req, timeout=None: Resp(json.dumps({"accessToken": TOKEN}).encode()))
    assert InfisicalClient("https://example.test").login_with_ttl("cid", "sec") == (TOKEN, None)
