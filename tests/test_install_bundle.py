"""`ae bootstrap --seed` materializes the AGENT_* instruction bundle (issue #60).

The provider client is faked: these tests pin the layout, canonical form,
idempotency, backups, degraded mode, and that no bundle value is ever printed.
"""
import hashlib
import io
import json
import os
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from agent_extensions.install import MANAGED_MD_MARKER

SEED = "INFISICAL_CLIENT_ID=cid-123\nINFISICAL_CLIENT_SECRET=sec-ret-value\nINFISICAL_PROJECT_ID=proj-9\n"

# Distinctive fake values so a leak into any output is unmistakable.
VALUES = {
    "AGENT_GLOBAL_RULES_MD": "# Global rules\r\nLEAKCANARY-RULES\r\n\r\n",
    "AGENT_HARNESS_CONFIG_YAML": "providers:\n  secret: LEAKCANARY-CONFIG",
    "AGENT_HARNESS_README_MD": "# Harness README\nLEAKCANARY-README\n",
    "AGENT_HARNESS_AGENTS_MD": "# Harness dir\nLEAKCANARY-DIR",
    "AGENT_FETCH_AGENT_SECRETS_PS1": "Write-Output 'LEAKCANARY-PS1'\n\n",
    "AGENT_MINT_GITHUB_APP_TOKEN_MJS": "console.log('LEAKCANARY-MJS')",
}

EXPECTED_PATHS = {
    "AGENT_GLOBAL_RULES_MD": ("AGENTS.md",),
    "AGENT_HARNESS_CONFIG_YAML": (".config", "agents", "config.yaml"),
    "AGENT_HARNESS_README_MD": (".config", "agents", "README.md"),
    "AGENT_HARNESS_AGENTS_MD": (".config", "agents", "AGENTS.md"),
    "AGENT_FETCH_AGENT_SECRETS_PS1": (".config", "agents", "fetch-agent-secrets.ps1"),
    "AGENT_MINT_GITHUB_APP_TOKEN_MJS": (".config", "agents", "mint-github-app-token.mjs"),
}


class FakeClient:
    """Records calls; returns the bundle or raises, never touching a network."""

    def __init__(self, values=None, error=None):
        self.values = dict(VALUES if values is None else values)
        self.error = error
        self.calls = []

    def login(self, client_id, client_secret):
        self.calls.append(("login", client_id))
        if self.error:
            raise self.error
        return "fake-token"

    def fetch_root(self, token, project_id):
        self.calls.append(("fetch_root", project_id))
        return dict(self.values)


@pytest.fixture()
def home(tmp_path, monkeypatch):
    h = tmp_path / "home"
    h.mkdir()
    monkeypatch.setenv("HOME", str(h))
    monkeypatch.setenv("USERPROFILE", str(h))
    for key in ("INFISICAL_CLIENT_ID", "INFISICAL_CLIENT_SECRET", "INFISICAL_PROJECT_ID"):
        monkeypatch.delenv(key, raising=False)
    return h


@pytest.fixture()
def seed(tmp_path):
    p = tmp_path / "seed.env"
    p.write_text(SEED, encoding="utf-8")
    return p


def _bundle():
    from agent_extensions.install import bundle

    return bundle


def _run(home, seed=None, client=None, env=None, now="20261027T070000Z"):
    b = _bundle()
    c = client or FakeClient()
    result = b.stage_bundle(home, seed_path=seed, env=env if env is not None else {}, client=c, now=now)
    return result, c


def test_layout_maps_all_six_keys_to_home_templated_paths():
    """Each AGENT_* key has exactly one $HOME-relative target, matching the bundle README layout."""
    b = _bundle()
    assert {k: tuple(v) for k, v in b.BUNDLE_LAYOUT.items()} == EXPECTED_PATHS
    for parts in b.BUNDLE_LAYOUT.values():
        assert not any("Users" in part or ":" in part or "\\" in part for part in parts)


def test_parse_seed_reads_the_three_lines_and_tolerates_crlf_comments_blanks():
    """The 3-line machine-identity seed parses from KEY=VALUE lines, CRLF and comments allowed."""
    b = _bundle()
    parsed = b.parse_seed("# seed\r\nINFISICAL_CLIENT_ID=a\r\n\r\nINFISICAL_CLIENT_SECRET=b\r\nINFISICAL_PROJECT_ID=c\r\n")
    assert parsed == {"INFISICAL_CLIENT_ID": "a", "INFISICAL_CLIENT_SECRET": "b", "INFISICAL_PROJECT_ID": "c"}


def test_parse_seed_names_a_missing_key_without_echoing_any_value():
    """A partial seed fails naming the missing key; the present secret never appears in the error."""
    b = _bundle()
    with pytest.raises(b.SeedError) as exc:
        b.parse_seed("INFISICAL_CLIENT_ID=a\nINFISICAL_CLIENT_SECRET=topsecret\n")
    assert "INFISICAL_PROJECT_ID" in str(exc.value)
    assert "topsecret" not in str(exc.value)


def test_canonical_form_is_lf_with_exactly_one_trailing_newline():
    """Values are LF-normalized and trailing newlines collapse to one, re-adding the one the CLI trims."""
    b = _bundle()
    assert b.canonical("a\r\nb\r\n\r\n") == "a\nb\n"
    assert b.canonical("a\nb") == "a\nb\n"
    assert b.canonical("a\rb") == "a\nb\n"


def test_materializes_all_six_keys_in_canonical_form(home, seed):
    """A seeded run writes every key to its path, canonicalized, and reports applied."""
    result, client = _run(home, seed)
    assert result.status == "applied", result.detail
    b = _bundle()
    for key, parts in EXPECTED_PATHS.items():
        target = home.joinpath(*parts)
        assert target.read_text(encoding="utf-8") == b.canonical(VALUES[key])
    assert ("login", "cid-123") in client.calls and ("fetch_root", "proj-9") in client.calls


def test_receipt_records_path_and_sha16_of_each_materialized_file(home, seed):
    """The receipt lists each key's path and sha16 of the file bytes, never a value."""
    _run(home, seed)
    receipt = json.loads((home / ".config" / "agents" / "bundle-materialize-receipt.json").read_text(encoding="utf-8"))
    for key, parts in EXPECTED_PATHS.items():
        entry = receipt["keys"][key]
        data = home.joinpath(*parts).read_bytes()
        assert entry["sha16"] == hashlib.sha256(data).hexdigest()[:16]
        assert entry["path"] == "~/" + "/".join(parts)
    assert "LEAKCANARY" not in json.dumps(receipt)


def test_rerun_is_idempotent_no_writes_no_backups(home, seed):
    """A second run with the same bundle changes nothing and creates no backup."""
    _run(home, seed, now="20261027T070000Z")
    stamps = {k: home.joinpath(*p).stat().st_mtime_ns for k, p in EXPECTED_PATHS.items()}
    result, _ = _run(home, seed, now="20261027T070500Z")
    assert result.status == "applied" and "up to date" in result.detail
    assert stamps == {k: home.joinpath(*p).stat().st_mtime_ns for k, p in EXPECTED_PATHS.items()}
    assert not (home / ".config" / "agents" / "backups").exists()


def test_changed_value_backs_up_the_previous_file_before_overwriting(home, seed):
    """An updated key first copies the old file under backups/<timestamp>/, then writes the new one."""
    _run(home, seed, now="20261027T070000Z")
    changed = dict(VALUES, AGENT_HARNESS_CONFIG_YAML="providers:\n  secret: NEWER")
    _run(home, seed, client=FakeClient(changed), now="20261027T080000Z")
    backup = home / ".config" / "agents" / "backups" / "20261027T080000Z" / ".config" / "agents" / "config.yaml"
    b = _bundle()
    assert backup.read_text(encoding="utf-8") == b.canonical(VALUES["AGENT_HARNESS_CONFIG_YAML"])
    assert (home / ".config" / "agents" / "config.yaml").read_text(encoding="utf-8") == "providers:\n  secret: NEWER\n"


def test_no_seed_and_no_env_skips_and_names_every_key(home):
    """Degraded mode: with no seed the stage is skipped, never failed, and lists each skipped key."""
    result, client = _run(home, seed=None, env={})
    assert result.status == "skipped"
    for key in EXPECTED_PATHS:
        assert key in result.detail
    assert client.calls == []


def test_env_identity_is_used_when_no_seed_file_is_given(home):
    """A session with INFISICAL_CLIENT_ID/SECRET/PROJECT_ID in its environment needs no seed file."""
    env = {"INFISICAL_CLIENT_ID": "env-id", "INFISICAL_CLIENT_SECRET": "env-secret", "INFISICAL_PROJECT_ID": "env-proj"}
    result, client = _run(home, seed=None, env=env)
    assert result.status == "applied", result.detail
    assert ("login", "env-id") in client.calls and ("fetch_root", "env-proj") in client.calls


def test_provider_failure_fails_the_stage_without_leaking(home, seed):
    """A provider error fails this stage only, names the error, and leaks no secret."""
    result, _ = _run(home, seed, client=FakeClient(error=RuntimeError("HTTP 401 from provider")))
    assert result.status == "failed"
    assert "401" in result.detail
    assert "sec-ret-value" not in result.detail
    assert not (home / "AGENTS.md").exists()


def test_missing_keys_fail_naming_them_while_present_keys_still_land(home, seed):
    """A bundle missing a key fails naming it, and every present key is still materialized."""
    partial = {k: v for k, v in VALUES.items() if k != "AGENT_MINT_GITHUB_APP_TOKEN_MJS"}
    result, _ = _run(home, seed, client=FakeClient(partial))
    assert result.status == "failed"
    assert "AGENT_MINT_GITHUB_APP_TOKEN_MJS" in result.detail
    assert (home / ".config" / "agents" / "config.yaml").exists()


def test_no_bundle_value_or_seed_secret_appears_in_the_report(home, seed, capsys):
    """Reports carry key names, paths and hashes only: no bundle value and no seed secret."""
    result, _ = _run(home, seed)
    shown = result.detail + capsys.readouterr().out
    assert "LEAKCANARY" not in shown
    assert "sec-ret-value" not in shown


@pytest.mark.skipif(os.name == "nt", reason="POSIX file modes")
def test_materialized_files_are_owner_only(home, seed):
    """Bundle files are private machine config: mode 0600 on POSIX."""
    _run(home, seed)
    for parts in EXPECTED_PATHS.values():
        assert (home.joinpath(*parts).stat().st_mode & 0o777) == 0o600


def test_bundle_global_rules_are_never_clobbered_by_the_baseline_shim(home, seed):
    """The owner's bundle owns ~/AGENTS.md; the managed baseline template then skips it as unmanaged."""
    from agent_extensions.install.machine import stage_shims

    repo = Path(__file__).resolve().parents[1]
    _run(home, seed)
    before = (home / "AGENTS.md").read_text(encoding="utf-8")
    assert MANAGED_MD_MARKER not in before
    shims = stage_shims(repo, home)
    assert "baseline: skipped (unmanaged" in shims.detail
    assert (home / "AGENTS.md").read_text(encoding="utf-8") == before


def test_bootstrap_runs_the_bundle_stage_first_and_passes_the_seed(home, seed, monkeypatch):
    """`bootstrap(..., seed=)` runs the bundle stage before the shims, so a fresh machine never writes the template first."""
    from agent_extensions.install import machine
    from agent_extensions.sync.bootstrap import StageResult

    seen = []

    def fake_stage_bundle(h, **kw):
        seen.append(("bundle", kw.get("seed_path")))
        return StageResult(name="bundle", status="skipped", detail="x")

    monkeypatch.setattr(machine, "stage_bundle", fake_stage_bundle)
    report = machine.bootstrap(Path(__file__).resolve().parents[1], home=home, env={}, seed=seed)
    assert seen == [("bundle", seed)]
    assert [s.name for s in report.stages][:2] == ["bundle", "gitignore"]


def test_cli_accepts_seed_flag(monkeypatch, tmp_path, home):
    """`ae bootstrap --seed <file>` forwards the seed path to bootstrap."""
    from agent_extensions.install import __main__ as cli
    from agent_extensions.install import machine
    from agent_extensions.sync.bootstrap import BootstrapReport

    seen = {}

    def fake_bootstrap(install_repo, home=None, env=None, seed=None):
        seen["seed"] = seed
        return BootstrapReport()

    monkeypatch.setattr(machine, "bootstrap", fake_bootstrap)
    seed_file = tmp_path / "s.env"
    seed_file.write_text(SEED, encoding="utf-8")
    assert cli.main(["bootstrap", "--seed", str(seed_file)]) == 0
    assert Path(seen["seed"]) == seed_file


def test_real_client_speaks_the_infisical_api_and_redacts(monkeypatch):
    """InfisicalClient posts universal-auth login and reads root secrets; errors never carry the secret."""
    b = _bundle()

    class Resp(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def fake_urlopen(req, timeout=None):
        if req.full_url.endswith("/api/v1/auth/universal-auth/login"):
            body = json.loads(req.data.decode("utf-8"))
            assert body == {"clientId": "cid", "clientSecret": "s3cret"}
            return Resp(json.dumps({"accessToken": "tok"}).encode())
        assert req.get_header("Authorization") == "Bearer tok"
        assert "workspaceId=proj" in req.full_url and "environment=prod" in req.full_url
        return Resp(json.dumps({"secrets": [{"secretKey": "AGENT_HARNESS_README_MD", "secretValue": "x"},
                                            {"secretKey": "OTHER", "secretValue": "y"}]}).encode())

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    client = b.InfisicalClient("https://app.infisical.com")
    token = client.login("cid", "s3cret")
    assert client.fetch_root(token, "proj") == {"AGENT_HARNESS_README_MD": "x"}, "only AGENT_* keys are returned"

    def failing(req, timeout=None):
        raise urllib.error.HTTPError(req.full_url, 401, "Unauthorized", {}, None)

    monkeypatch.setattr(urllib.request, "urlopen", failing)
    with pytest.raises(b.ProviderError) as exc:
        client.login("cid", "s3cret")
    assert "401" in str(exc.value) and "s3cret" not in str(exc.value)


def test_an_error_that_echoes_the_seed_secret_is_redacted(home, seed):
    """A provider error whose text contains the client secret is redacted before it reaches the report."""
    result, _ = _run(home, seed, client=FakeClient(error=RuntimeError("login refused for sec-ret-value")))
    assert result.status == "failed"
    assert "sec-ret-value" not in result.detail and "[redacted]" in result.detail


def test_cli_bundle_runs_only_the_bundle_stage(monkeypatch, tmp_path, home, capsys):
    """`ae bundle` runs the single bundle stage (for session start); a skip exits 0, a failure exits 1."""
    from agent_extensions.install import __main__ as cli
    from agent_extensions.install import bundle
    from agent_extensions.sync.bootstrap import StageResult

    seen = {}

    def fake_stage(home, **kw):
        seen.update(kw)
        return StageResult(name="bundle", status=seen.get("_status", "skipped"), detail="no identity")

    monkeypatch.setattr(bundle, "stage_bundle", fake_stage)
    assert cli.main(["bundle"]) == 0
    assert "skipped  bundle: no identity" in capsys.readouterr().out
    assert seen["seed_path"] is None

    def failing_stage(home, **kw):
        return StageResult(name="bundle", status="failed", detail="secret provider returned HTTP 401")

    monkeypatch.setattr(bundle, "stage_bundle", failing_stage)
    assert cli.main(["bundle", "--seed", str(tmp_path / "s.env")]) == 1
