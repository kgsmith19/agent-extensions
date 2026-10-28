"""RED: clean-room harness drivers for the benchmark (#91).

Contract under test (canary/benchmark/adapters/, not written yet):

  adapters.DRIVERS                 dict[str, type]; exactly the keys below.
  Driver.name                      equals its DRIVERS key and the trial `harness`.
  Driver.command(checkpoint, model_base_url, clean_home) -> (argv, env)
                                   pure; no CLI is run.
  Driver.prepare(checkpoint, clean_home), Driver.run(model), Driver.collect() -> trial dict,
  Driver.gaps() -> [{"id", "reason"}].

Model object passed to run(model): {"alias": <str>, "base_url": <str>}. The
base URL is the double's root (no path); each driver appends its own path.

Fake CLIs: each collect() test puts an executable named like the real CLI on
PATH. It prints a canned stdout (a fixture string in this module) and touches
allowed.txt in its working directory, which the driver sets to the workspace.

Hermetic: no npm, no real CLI, no network, no Infisical.
"""

import hashlib
import os
from pathlib import Path

import pytest

from agent_extensions.sync.harness import ADAPTERS
from canary.benchmark import adapters

CHECKPOINT = {"id": "cr.R1.implement", "fixture": {"head_sha": "a" * 40}}
BASE = "http://127.0.0.1:43210"
ALIAS = "scripted/m1"

DRIVER_NAMES = ("claude-code", "codex", "gemini-cli", "kilo")
ADAPTER_KEYS_PINNED = [  # tests/test_harness_adapters.py:328, unchanged by #91
    "antigravity", "claude-code", "cline", "codex", "generic", "kilo", "local", "opencode", "pi",
]
SENTINEL_ANTHROPIC = "sk-ant-sentinel-must-not-leak"
SENTINEL_OPENAI = "sk-openai-sentinel-must-not-leak"
SENTINEL_GOOGLE = "google-sentinel-must-not-leak"
LEAK_MARKER_VAR = "AE_CLEAN_ROOM_LEAK_MARKER"
HOME_VARS = ("HOME", "XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_CACHE_HOME", "XDG_STATE_HOME")

# --- canned CLI stdout (the raw log; its sha256 is the expected raw_log_digest) ----------

CLAUDE_STREAM_JSON = (
    '{"type":"system","subtype":"init","session_id":"s1","model":"m1"}\n'
    '{"type":"assistant","message":{"role":"assistant","content":[{"type":"tool_use",'
    '"id":"t1","name":"Write","input":{"file_path":"allowed.txt","content":"hello\\n"}}]}}\n'
    '{"type":"user","message":{"role":"user","content":[{"type":"tool_result",'
    '"tool_use_id":"t1","content":"ok"}]}}\n'
    '{"type":"assistant","message":{"role":"assistant","content":[{"type":"text","text":"done"}]}}\n'
    '{"type":"result","subtype":"success","is_error":false,"result":"done",'
    '"total_cost_usd":0.0,"usage":{"input_tokens":31,"output_tokens":5}}\n'
).encode("utf-8")

CODEX_EXEC_JSONL = (
    '{"type":"thread.started","thread_id":"th1"}\n'
    '{"type":"turn.started"}\n'
    '{"type":"item.completed","item":{"id":"i1","type":"file_change","status":"completed",'
    '"changes":[{"path":"allowed.txt","kind":"add"}]}}\n'
    '{"type":"item.completed","item":{"id":"i2","type":"agent_message","text":"done"}}\n'
    '{"type":"turn.completed","usage":{"input_tokens":40,"cached_input_tokens":0,'
    '"output_tokens":7}}\n'
).encode("utf-8")

GEMINI_STREAM_JSON = (
    '{"type":"init","session_id":"g1","model":"m1"}\n'
    '{"type":"tool_use","tool_name":"write_file","tool_id":"w1",'
    '"parameters":{"file_path":"allowed.txt","content":"hello\\n"}}\n'
    '{"type":"tool_result","tool_id":"w1","status":"success"}\n'
    '{"type":"message","role":"assistant","content":"done"}\n'
    '{"type":"result","status":"success","stats":{"input_tokens":25,"output_tokens":4}}\n'
).encode("utf-8")

KILO_RUN_JSON = (
    '{"type":"step_start","part":{"type":"step-start"}}\n'
    '{"type":"tool_use","part":{"type":"tool","tool":"write","state":{"status":"completed",'
    '"input":{"filePath":"allowed.txt","content":"hello\\n"}}}}\n'
    '{"type":"step_finish","part":{"type":"step-finish","reason":"tool-calls",'
    '"cost":0,"tokens":{"input":12,"output":3}}}\n'
    '{"type":"text","part":{"type":"text","text":"done"}}\n'
    '{"type":"step_finish","part":{"type":"step-finish","reason":"stop",'
    '"cost":0,"tokens":{"input":18,"output":2}}}\n'
).encode("utf-8")

# driver name -> (executable, canned stdout, provider, (tokens_in, tokens_out))
FAKE_CLIS = {
    "claude-code": ("claude", CLAUDE_STREAM_JSON, "claude", (31, 5)),
    "codex": ("codex", CODEX_EXEC_JSONL, "codex", (40, 7)),
    "gemini-cli": ("gemini", GEMINI_STREAM_JSON, "gemini", (25, 4)),
    "kilo": ("kilo", KILO_RUN_JSON, "kilo", (30, 5)),
}

TRIAL_FIELDS = (
    "harness", "provider", "model_alias", "first_action", "files_touched",
    "tokens_in", "tokens_out", "latency_ms", "cost_usd", "interventions", "raw_log_digest",
)


# --- helpers ---------------------------------------------------------------------------


def _model(base_url=BASE):
    return {"alias": ALIAS, "base_url": base_url}


def _driver(name):
    return adapters.DRIVERS[name]()


def _inside(path, root):
    return Path(path).resolve().is_relative_to(Path(root).resolve())


def _write_fake_cli(bin_dir, exe, stdout):
    """Executable `exe` that prints `stdout` and touches allowed.txt in its cwd."""
    bin_dir.mkdir(parents=True, exist_ok=True)
    canned = bin_dir / f"{exe}.canned-stdout"
    canned.write_bytes(stdout)
    fake = bin_dir / exe
    fake.write_text(f'#!/bin/sh\ncat "{canned}"\ntouch "$PWD/allowed.txt"\n', encoding="utf-8")
    fake.chmod(0o755)
    return fake


def _put_on_path(monkeypatch, bin_dir):
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")


def _outcome_of_run_and_collect(driver, model):
    """The driver's answer when its CLI is absent: a raised error, a gap dict, or a trial."""
    try:
        ran = driver.run(model)
    except Exception as exc:  # the declared gap may be raised; inspected below
        return exc
    if isinstance(ran, dict) and "gap_id" in ran:
        return ran
    try:
        return driver.collect()
    except Exception as exc:
        return exc


# --- registry --------------------------------------------------------------------------


def test_drivers_registry_has_exactly_the_four_cli_drivers():
    assert set(adapters.DRIVERS) == set(DRIVER_NAMES)
    assert len(adapters.DRIVERS) == 4


@pytest.mark.parametrize("name", DRIVER_NAMES)
def test_each_driver_name_matches_its_registry_key(name):
    driver = _driver(name)
    assert driver.name == name


@pytest.mark.parametrize("name", DRIVER_NAMES)
def test_each_driver_exposes_the_protocol_methods(name):
    driver = _driver(name)
    for method in ("prepare", "run", "collect", "gaps", "command"):
        assert callable(getattr(driver, method, None)), (name, method)


# --- command(): argv pins --------------------------------------------------------------


def test_claude_command_is_headless_stream_json_with_bare():
    argv, _ = _driver("claude-code").command(CHECKPOINT, BASE, Path("/unused"))
    assert argv[:5] == ["claude", "-p", "--output-format", "stream-json", "--bare"]


def test_codex_command_is_exec_json():
    argv, _ = _driver("codex").command(CHECKPOINT, BASE, Path("/unused"))
    assert argv[:3] == ["codex", "exec", "--json"]


def test_gemini_command_is_headless_prompt_with_stream_json_output():
    argv, _ = _driver("gemini-cli").command(CHECKPOINT, BASE, Path("/unused"))
    assert argv[0] == "gemini"
    assert "-p" in argv
    flag = argv.index("--output-format")
    assert argv[flag + 1] == "stream-json"


def test_kilo_command_is_run_with_json_format():
    argv, _ = _driver("kilo").command(CHECKPOINT, BASE, Path("/unused"))
    assert argv[:4] == ["kilo", "run", "--format", "json"]


# --- command(): clean-room env pins -----------------------------------------------------


@pytest.mark.parametrize("name", DRIVER_NAMES)
def test_home_and_xdg_dirs_point_inside_clean_home(name, tmp_path):
    clean_home = tmp_path / "clean-home"
    clean_home.mkdir()
    _, env = _driver(name).command(CHECKPOINT, BASE, clean_home)
    for var in HOME_VARS:
        assert var in env, (name, var)
        assert _inside(env[var], clean_home), (name, var, env[var])


def test_codex_home_points_inside_clean_home(tmp_path):
    clean_home = tmp_path / "clean-home"
    clean_home.mkdir()
    _, env = _driver("codex").command(CHECKPOINT, BASE, clean_home)
    assert _inside(env["CODEX_HOME"], clean_home)


@pytest.mark.parametrize("name", DRIVER_NAMES)
def test_real_api_key_sentinels_in_os_environ_never_reach_the_cli_env(name, tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", SENTINEL_ANTHROPIC)
    monkeypatch.setenv("OPENAI_API_KEY", SENTINEL_OPENAI)
    monkeypatch.setenv("GEMINI_API_KEY", SENTINEL_GOOGLE)
    monkeypatch.setenv("GOOGLE_API_KEY", SENTINEL_GOOGLE)
    clean_home = tmp_path / "clean-home"
    clean_home.mkdir()
    _, env = _driver(name).command(CHECKPOINT, BASE, clean_home)
    assert env.get("ANTHROPIC_API_KEY") != SENTINEL_ANTHROPIC
    assert env.get("OPENAI_API_KEY") != SENTINEL_OPENAI
    leaked = [k for k, v in env.items()
              if v in (SENTINEL_ANTHROPIC, SENTINEL_OPENAI, SENTINEL_GOOGLE)]
    assert leaked == []


@pytest.mark.parametrize("name", DRIVER_NAMES)
def test_env_is_built_from_scratch_not_inherited_wholesale(name, tmp_path, monkeypatch):
    monkeypatch.setenv(LEAK_MARKER_VAR, "inherited")
    clean_home = tmp_path / "clean-home"
    clean_home.mkdir()
    _, env = _driver(name).command(CHECKPOINT, BASE, clean_home)
    assert LEAK_MARKER_VAR not in env
    assert "PATH" in env  # the CLI needs PATH to find its own runtime


def test_claude_env_points_its_api_base_at_the_double(tmp_path):
    clean_home = tmp_path / "clean-home"
    clean_home.mkdir()
    _, env = _driver("claude-code").command(CHECKPOINT, BASE, clean_home)
    assert env["ANTHROPIC_BASE_URL"] == BASE


def test_gemini_env_points_its_api_base_at_the_double(tmp_path):
    clean_home = tmp_path / "clean-home"
    clean_home.mkdir()
    _, env = _driver("gemini-cli").command(CHECKPOINT, BASE, clean_home)
    assert env["GOOGLE_GEMINI_BASE_URL"] == BASE


@pytest.mark.parametrize("name", ("codex", "kilo"))
def test_openai_wire_drivers_carry_the_double_url_in_argv_or_env(name, tmp_path):
    clean_home = tmp_path / "clean-home"
    clean_home.mkdir()
    argv, env = _driver(name).command(CHECKPOINT, BASE, clean_home)
    carried = [*argv, *env.values()]
    assert any(BASE in str(value) for value in carried), name


# --- gaps() ----------------------------------------------------------------------------


def test_kilo_gaps_are_the_harness_adapter_gaps():
    gaps = _driver("kilo").gaps()
    assert [g["id"] for g in gaps] == ADAPTERS["kilo"].gaps()
    assert all(g["reason"] for g in gaps)


def test_codex_gaps_are_the_harness_adapter_gaps():
    gaps = _driver("codex").gaps()
    assert [g["id"] for g in gaps] == ADAPTERS["codex"].gaps() == ["hook-inheritance", "sandbox"]
    assert all(g["reason"] == ADAPTERS["codex"].gap_reason for g in gaps)


def test_claude_gaps_are_the_harness_gaps_plus_hooks_not_run():
    gaps = _driver("claude-code").gaps()
    assert [g["id"] for g in gaps] == [*ADAPTERS["claude-code"].gaps(), "hooks-not-run"]
    hooks = next(g for g in gaps if g["id"] == "hooks-not-run")
    assert hooks["reason"]


def test_gemini_gaps_declare_the_surface_as_undeclared():
    gaps = _driver("gemini-cli").gaps()
    assert [g["id"] for g in gaps] == ["harness-surface-undeclared"]
    assert gaps[0]["reason"]


def test_no_driver_edits_the_harness_adapter_registry():
    before = sorted(ADAPTERS)
    for name in DRIVER_NAMES:
        _driver(name).gaps()
    assert sorted(ADAPTERS) == ADAPTER_KEYS_PINNED == before
    assert "gemini-cli" not in ADAPTERS


# --- collect(): fake CLI maps into a trial ----------------------------------------------


@pytest.mark.parametrize("name", DRIVER_NAMES)
def test_collect_maps_a_fake_cli_run_into_trial_fields(name, tmp_path, monkeypatch):
    exe, stdout, provider, (tokens_in, tokens_out) = FAKE_CLIS[name]
    bin_dir = tmp_path / "bin"
    _write_fake_cli(bin_dir, exe, stdout)
    _put_on_path(monkeypatch, bin_dir)
    clean_home = tmp_path / "clean-home"
    clean_home.mkdir()

    driver = _driver(name)
    driver.prepare(CHECKPOINT, clean_home)
    driver.run(_model())
    trial = driver.collect()

    for field in TRIAL_FIELDS:
        assert field in trial, (name, field)
    assert trial["harness"] == name
    assert trial["provider"] == provider
    assert trial["model_alias"] == ALIAS
    assert trial["first_action"] == {"verb": "write", "target": "allowed.txt"}
    assert trial["files_touched"] == ["allowed.txt"]
    assert trial["tokens_in"] == tokens_in
    assert trial["tokens_out"] == tokens_out
    assert isinstance(trial["latency_ms"], int) and trial["latency_ms"] >= 0
    assert trial["interventions"] == 0


@pytest.mark.parametrize("name", DRIVER_NAMES)
def test_collect_digests_the_real_captured_log_bytes(name, tmp_path, monkeypatch):
    exe, stdout, _, _ = FAKE_CLIS[name]
    bin_dir = tmp_path / "bin"
    _write_fake_cli(bin_dir, exe, stdout)
    _put_on_path(monkeypatch, bin_dir)
    clean_home = tmp_path / "clean-home"
    clean_home.mkdir()

    driver = _driver(name)
    driver.prepare(CHECKPOINT, clean_home)
    driver.run(_model())
    trial = driver.collect()

    expected = "sha256:" + hashlib.sha256(stdout).hexdigest()
    assert trial["raw_log_digest"] == expected
    # Never the checkpoint id (the std oracle's stand-in digest).
    assert trial["raw_log_digest"] != "sha256:" + hashlib.sha256(
        CHECKPOINT["id"].encode("utf-8")).hexdigest()


@pytest.mark.parametrize("name", DRIVER_NAMES)
def test_fake_cli_writes_only_inside_clean_home(name, tmp_path, monkeypatch):
    exe, stdout, _, _ = FAKE_CLIS[name]
    bin_dir = tmp_path / "bin"
    _write_fake_cli(bin_dir, exe, stdout)
    _put_on_path(monkeypatch, bin_dir)
    clean_home = tmp_path / "clean-home"
    clean_home.mkdir()

    driver = _driver(name)
    driver.prepare(CHECKPOINT, clean_home)
    driver.run(_model())

    touched = [p for p in clean_home.rglob("allowed.txt") if p.is_file()]
    assert touched, f"{name}: the CLI did not run inside the clean home workspace"
    assert not (tmp_path / "bin" / "allowed.txt").exists()


def test_kilo_renders_the_scripted_provider_block_into_clean_home(tmp_path, monkeypatch):
    from canary.kilo.run_canary import PROVIDER_BLOCK

    exe, stdout, _, _ = FAKE_CLIS["kilo"]
    bin_dir = tmp_path / "bin"
    _write_fake_cli(bin_dir, exe, stdout)
    _put_on_path(monkeypatch, bin_dir)
    clean_home = tmp_path / "clean-home"
    clean_home.mkdir()

    driver = _driver("kilo")
    driver.prepare(CHECKPOINT, clean_home)
    driver.run(_model())

    npm = PROVIDER_BLOCK["scripted"]["npm"]
    rendered = [p.read_text(encoding="utf-8", errors="ignore")
                for p in clean_home.rglob("*") if p.is_file()]
    assert any(npm in text and f"{BASE}/v1" in text for text in rendered)


# --- missing CLI: a declared gap, never a fake trial -----------------------------------


@pytest.mark.parametrize("name", DRIVER_NAMES)
def test_missing_cli_yields_declared_gap_not_a_trial(name, tmp_path, monkeypatch):
    empty_bin = tmp_path / "empty-bin"
    empty_bin.mkdir()
    monkeypatch.setenv("PATH", str(empty_bin))
    clean_home = tmp_path / "clean-home"
    clean_home.mkdir()

    driver = _driver(name)
    driver.prepare(CHECKPOINT, clean_home)
    outcome = _outcome_of_run_and_collect(driver, _model())

    if isinstance(outcome, BaseException):
        gap_id = getattr(outcome, "gap_id", None)
    else:
        gap_id = outcome.get("gap_id") if isinstance(outcome, dict) else None
    assert gap_id == "cli-not-installed", (name, outcome)
    assert not (isinstance(outcome, dict) and "raw_log_digest" in outcome), name


def test_missing_cli_gap_is_not_reported_as_a_pass_or_a_trial_for_any_driver(tmp_path, monkeypatch):
    empty_bin = tmp_path / "empty-bin"
    empty_bin.mkdir()
    monkeypatch.setenv("PATH", str(empty_bin))
    clean_home = tmp_path / "clean-home"
    clean_home.mkdir()
    for name in DRIVER_NAMES:
        driver = _driver(name)
        driver.prepare(CHECKPOINT, clean_home)
        outcome = _outcome_of_run_and_collect(driver, _model())
        verdict = outcome.get("verdict") if isinstance(outcome, dict) else None
        assert verdict != "pass", name
        assert not isinstance(outcome, dict) or "tokens_in" not in outcome, name
