"""Clean-room harness drivers for the benchmark (#91).

Contract under test (canary/benchmark/adapters/):

  adapters.DRIVERS                 dict[str, type]; exactly the keys below.
  Driver.name                      equals its DRIVERS key and the trial `harness`.
  Driver.command(checkpoint, model_base_url, clean_home) -> (argv, env)
                                   pure; no CLI is run. The prompt is core.build_prompt.
  Driver.prepare(checkpoint, clean_home), Driver.run(model), Driver.collect() -> observation
                                   (a trial dict plus transcript_text).
  Driver.gaps() -> [{"id", "reason"}].

Model object passed to run(model): {"alias": <str>, "base_url": <str>}. The
base URL is the double's root (no path); each driver appends its own path.

Fake CLIs (tests/fixtures/clean_room/fake_clis.py) print canned output, so every
run here is hermetic: no npm, no real CLI, no network, no Infisical.
"""

import hashlib
import os
from pathlib import Path

import pytest

from agent_extensions.sync.harness import ADAPTERS
from canary.benchmark import adapters, core
from tests.fixtures.clean_room.fake_clis import (
    CHECKPOINT,
    CODEX_COMMAND_JSONL,
    FAKE_CLIS,
    write_fake_cli,
)

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
TRIAL_FIELDS = (
    "harness", "provider", "model_alias", "first_action", "files_touched",
    "tokens_in", "tokens_out", "latency_ms", "cost_usd", "interventions", "raw_log_digest",
)
# D5: the claude canned write is a Bash touch; the others are native writes.
FIRST_ACTIONS = {
    "claude-code": {"verb": "bash", "target": "touch allowed.txt"},
    "codex": {"verb": "write", "target": "allowed.txt"},
    "gemini-cli": {"verb": "write", "target": "allowed.txt"},
    "kilo": {"verb": "write", "target": "allowed.txt"},
}
SECRET = "sk-proj-SECRETVALUE1234567890"


# --- helpers ---------------------------------------------------------------------------


def _model(base_url=BASE):
    return {"alias": ALIAS, "base_url": base_url}


def _driver(name):
    return adapters.DRIVERS[name]()


def _inside(path, root):
    return Path(path).resolve().is_relative_to(Path(root).resolve())


def _put_on_path(monkeypatch, bin_dir):
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")


def _fake_on_path(monkeypatch, tmp_path, name, stdout=None, **kwargs):
    exe, canned, _, _ = FAKE_CLIS[name]
    bin_dir = tmp_path / "bin"
    write_fake_cli(bin_dir, exe, canned if stdout is None else stdout, **kwargs)
    _put_on_path(monkeypatch, bin_dir)


def _clean_home(tmp_path):
    clean_home = tmp_path / "clean-home"
    clean_home.mkdir()
    return clean_home


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


# --- D1: the prompt is core.build_prompt, in argv ------------------------------------------


@pytest.mark.parametrize("name", DRIVER_NAMES)
def test_every_driver_sends_build_prompt_in_argv(name, tmp_path):
    argv, _ = _driver(name).command(CHECKPOINT, BASE, _clean_home(tmp_path))
    prompt = core.build_prompt(CHECKPOINT)
    assert prompt in argv, name
    assert CHECKPOINT["fixture"]["prompt_text"] in argv[argv.index(prompt)], name


@pytest.mark.parametrize("name", DRIVER_NAMES)
def test_every_driver_refuses_a_checkpoint_without_prompt_text(name, tmp_path):
    checkpoint = {"id": "cr.R0.spec", "fixture": {"head_sha": "a" * 40}}
    with pytest.raises(ValueError, match="prompt_text"):
        _driver(name).command(checkpoint, BASE, _clean_home(tmp_path))


# --- command(): argv pins --------------------------------------------------------------


def test_claude_command_is_headless_stream_json_with_bare_and_verbose(tmp_path):
    argv, _ = _driver("claude-code").command(CHECKPOINT, BASE, _clean_home(tmp_path))
    assert argv[:5] == ["claude", "-p", "--output-format", "stream-json", "--bare"]
    assert "--verbose" in argv  # D5: stream-json with -p requires --verbose


def test_codex_command_is_exec_json(tmp_path):
    argv, _ = _driver("codex").command(CHECKPOINT, BASE, _clean_home(tmp_path))
    assert argv[:3] == ["codex", "exec", "--json"]


def test_gemini_command_is_headless_prompt_with_stream_json_output(tmp_path):
    argv, _ = _driver("gemini-cli").command(CHECKPOINT, BASE, _clean_home(tmp_path))
    assert argv[0] == "gemini"
    assert "-p" in argv
    flag = argv.index("--output-format")
    assert argv[flag + 1] == "stream-json"


def test_kilo_command_is_run_with_json_format(tmp_path):
    argv, _ = _driver("kilo").command(CHECKPOINT, BASE, _clean_home(tmp_path))
    assert argv[:4] == ["kilo", "run", "--format", "json"]


# --- command(): clean-room env pins -----------------------------------------------------


@pytest.mark.parametrize("name", DRIVER_NAMES)
def test_home_and_xdg_dirs_point_inside_clean_home(name, tmp_path):
    clean_home = _clean_home(tmp_path)
    _, env = _driver(name).command(CHECKPOINT, BASE, clean_home)
    for var in HOME_VARS:
        assert var in env, (name, var)
        assert _inside(env[var], clean_home), (name, var, env[var])


def test_codex_home_points_inside_clean_home(tmp_path):
    clean_home = _clean_home(tmp_path)
    _, env = _driver("codex").command(CHECKPOINT, BASE, clean_home)
    assert _inside(env["CODEX_HOME"], clean_home)


@pytest.mark.parametrize("name", DRIVER_NAMES)
def test_real_api_key_sentinels_in_os_environ_never_reach_the_cli_env(name, tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", SENTINEL_ANTHROPIC)
    monkeypatch.setenv("OPENAI_API_KEY", SENTINEL_OPENAI)
    monkeypatch.setenv("GEMINI_API_KEY", SENTINEL_GOOGLE)
    monkeypatch.setenv("GOOGLE_API_KEY", SENTINEL_GOOGLE)
    _, env = _driver(name).command(CHECKPOINT, BASE, _clean_home(tmp_path))
    assert env.get("ANTHROPIC_API_KEY") != SENTINEL_ANTHROPIC
    assert env.get("OPENAI_API_KEY") != SENTINEL_OPENAI
    leaked = [k for k, v in env.items()
              if v in (SENTINEL_ANTHROPIC, SENTINEL_OPENAI, SENTINEL_GOOGLE)]
    assert leaked == []


@pytest.mark.parametrize("name", DRIVER_NAMES)
def test_env_is_built_from_scratch_not_inherited_wholesale(name, tmp_path, monkeypatch):
    monkeypatch.setenv(LEAK_MARKER_VAR, "inherited")
    _, env = _driver(name).command(CHECKPOINT, BASE, _clean_home(tmp_path))
    assert LEAK_MARKER_VAR not in env
    assert "PATH" in env  # the CLI needs PATH to find its own runtime


def test_claude_env_points_its_api_base_at_the_double(tmp_path):
    _, env = _driver("claude-code").command(CHECKPOINT, BASE, _clean_home(tmp_path))
    assert env["ANTHROPIC_BASE_URL"] == BASE


def test_gemini_env_points_its_api_base_at_the_double(tmp_path):
    _, env = _driver("gemini-cli").command(CHECKPOINT, BASE, _clean_home(tmp_path))
    assert env["GOOGLE_GEMINI_BASE_URL"] == BASE


@pytest.mark.parametrize("name", ("codex", "kilo"))
def test_openai_wire_drivers_carry_the_double_url_in_argv_or_env(name, tmp_path):
    argv, env = _driver(name).command(CHECKPOINT, BASE, _clean_home(tmp_path))
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
    _, _, provider, (tokens_in, tokens_out) = FAKE_CLIS[name]
    _fake_on_path(monkeypatch, tmp_path, name)
    clean_home = _clean_home(tmp_path)

    driver = _driver(name)
    driver.prepare(CHECKPOINT, clean_home)
    driver.run(_model())
    observation = driver.collect()

    for field in TRIAL_FIELDS:
        assert field in observation, (name, field)
    assert observation["harness"] == name
    assert observation["provider"] == provider
    assert observation["model_alias"] == ALIAS
    assert observation["first_action"] == FIRST_ACTIONS[name]
    assert observation["files_touched"] == ["allowed.txt"]
    assert observation["tokens_in"] == tokens_in
    assert observation["tokens_out"] == tokens_out
    assert isinstance(observation["latency_ms"], int) and observation["latency_ms"] >= 0
    assert observation["interventions"] == 0


@pytest.mark.parametrize("name", DRIVER_NAMES)
def test_collect_digests_the_real_captured_log_bytes(name, tmp_path, monkeypatch):
    _, stdout, _, _ = FAKE_CLIS[name]
    _fake_on_path(monkeypatch, tmp_path, name)
    clean_home = _clean_home(tmp_path)

    driver = _driver(name)
    driver.prepare(CHECKPOINT, clean_home)
    driver.run(_model())
    observation = driver.collect()

    expected = "sha256:" + hashlib.sha256(stdout).hexdigest()
    assert observation["raw_log_digest"] == expected
    # Never the checkpoint id (the std oracle's stand-in digest).
    assert observation["raw_log_digest"] != "sha256:" + hashlib.sha256(
        CHECKPOINT["id"].encode("utf-8")).hexdigest()


@pytest.mark.parametrize("name", DRIVER_NAMES)
def test_fake_cli_writes_only_inside_clean_home(name, tmp_path, monkeypatch):
    _fake_on_path(monkeypatch, tmp_path, name)
    clean_home = _clean_home(tmp_path)

    driver = _driver(name)
    driver.prepare(CHECKPOINT, clean_home)
    driver.run(_model())

    touched = [p for p in clean_home.rglob("allowed.txt") if p.is_file()]
    assert touched, f"{name}: the CLI did not run inside the clean home workspace"
    assert not (tmp_path / "bin" / "allowed.txt").exists()


def test_kilo_renders_the_scripted_provider_block_into_clean_home(tmp_path, monkeypatch):
    from canary.kilo.run_canary import PROVIDER_BLOCK

    _fake_on_path(monkeypatch, tmp_path, "kilo")
    clean_home = _clean_home(tmp_path)

    driver = _driver("kilo")
    driver.prepare(CHECKPOINT, clean_home)
    driver.run(_model())

    npm = PROVIDER_BLOCK["scripted"]["npm"]
    rendered = [p.read_text(encoding="utf-8", errors="ignore")
                for p in clean_home.rglob("*") if p.is_file()]
    assert any(npm in text and f"{BASE}/v1" in text for text in rendered)


# --- D2: the acceptance attempt comes from the transcript; each fake CLI validates ----------


@pytest.mark.parametrize("name", DRIVER_NAMES)
def test_collect_exposes_the_agent_transcript_with_its_acceptance_block(name, tmp_path, monkeypatch):
    _fake_on_path(monkeypatch, tmp_path, name)
    clean_home = _clean_home(tmp_path)
    driver = _driver(name)
    driver.prepare(CHECKPOINT, clean_home)
    driver.run(_model())
    transcript = driver.collect()["transcript_text"]
    assert isinstance(transcript, str)
    assert transcript.count("```acceptance") == 1, name


@pytest.mark.parametrize("name", DRIVER_NAMES)
def test_a_full_fake_cli_trial_validates_against_the_trial_model(name, tmp_path, monkeypatch):
    _fake_on_path(monkeypatch, tmp_path, name)
    trial = core.run_trial(_driver(name), CHECKPOINT, _model(), _clean_home(tmp_path))
    assert "status" not in trial and "transcript_text" not in trial
    core.validate_trial(trial)
    assert trial["acceptance_attempt"]["role"] == "builder", name
    assert trial["acceptance_attempt"]["prompt_text"] == core.build_prompt(CHECKPOINT), name


# --- D5: codex command_execution and file_change both give files_touched ------------------


@pytest.mark.parametrize("canned, first", [
    (None, {"verb": "write", "target": "allowed.txt"}),  # file_change canned stream
    (CODEX_COMMAND_JSONL, {"verb": "exec", "target": "/bin/bash -lc 'touch allowed.txt'"}),
])
def test_codex_write_is_files_touched_whichever_item_shape_carries_it(canned, first,
                                                                    tmp_path, monkeypatch):
    stdout = canned if canned is not None else FAKE_CLIS["codex"][1]
    write_fake_cli(tmp_path / "bin", "codex", stdout)
    _put_on_path(monkeypatch, tmp_path / "bin")
    driver = _driver("codex")
    driver.prepare(CHECKPOINT, _clean_home(tmp_path))
    driver.run(_model())
    observation = driver.collect()
    assert observation["files_touched"] == ["allowed.txt"]
    assert observation["first_action"] == first


# --- D4: files_touched is the workspace diff, add/change/remove ---------------------------


@pytest.mark.parametrize("name", DRIVER_NAMES)
def test_files_touched_is_the_workspace_diff_for_add_change_and_remove(name, tmp_path, monkeypatch):
    # The canned events name only allowed.txt. The shell changes three more files; the diff
    # must report all of them, so an event-based answer fails here.
    shell = "printf 'new\\n' > added.txt; printf 'changed\\n' > changed.txt; rm -f gone.txt"
    _fake_on_path(monkeypatch, tmp_path, name, extra_sh=shell)
    clean_home = _clean_home(tmp_path)
    workspace = clean_home / "workspace"
    driver = _driver(name)
    driver.prepare(CHECKPOINT, clean_home)
    (workspace / "changed.txt").write_text("old\n", encoding="utf-8")
    (workspace / "gone.txt").write_text("bye\n", encoding="utf-8")
    driver.run(_model())
    observation = driver.collect()
    assert observation["files_touched"] == sorted(
        ["added.txt", "allowed.txt", "changed.txt", "gone.txt"]), name


# --- D6: a failed or incomplete CLI run is a gap, never a trial ---------------------------


@pytest.mark.parametrize("name", DRIVER_NAMES)
def test_non_zero_exit_is_a_cli_run_failed_gap_and_redacts_secrets(name, tmp_path, monkeypatch):
    exe = FAKE_CLIS[name][0]
    stderr = f"auth failed for key {SECRET}\n".encode("utf-8")
    _fake_on_path(monkeypatch, tmp_path, name, stdout=b"", exit_code=3, stderr=stderr)
    result = core.run_trial(_driver(name), CHECKPOINT, _model(), _clean_home(tmp_path))
    assert result["status"] == "gap", name
    gap = result["gap"]
    assert gap["id"] == "cli-run-failed", name
    assert gap["reason"].startswith(f"{exe} exited 3: "), gap["reason"]
    assert "tokens_in" not in result and "harness" not in result, name
    assert SECRET not in gap["reason"], name
    assert "[REDACTED]" in gap["reason"], name


@pytest.mark.parametrize("name", DRIVER_NAMES)
def test_a_run_without_its_terminal_event_is_a_gap_never_a_trial(name, tmp_path, monkeypatch):
    _fake_on_path(monkeypatch, tmp_path, name, stdout=b"")
    result = core.run_trial(_driver(name), CHECKPOINT, _model(), _clean_home(tmp_path))
    assert result["status"] == "gap", name
    assert result["gap"]["id"] == "cli-run-failed", name
    assert "tokens_in" not in result, name


# --- missing CLI: a declared gap, never a fake trial -----------------------------------


@pytest.mark.parametrize("name", DRIVER_NAMES)
def test_missing_cli_yields_declared_gap_not_a_trial(name, tmp_path, monkeypatch):
    empty_bin = tmp_path / "empty-bin"
    empty_bin.mkdir()
    monkeypatch.setenv("PATH", str(empty_bin))
    clean_home = _clean_home(tmp_path)

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
    clean_home = _clean_home(tmp_path)
    for name in DRIVER_NAMES:
        driver = _driver(name)
        driver.prepare(CHECKPOINT, clean_home)
        outcome = _outcome_of_run_and_collect(driver, _model())
        verdict = outcome.get("verdict") if isinstance(outcome, dict) else None
        assert verdict != "pass", name
        assert not isinstance(outcome, dict) or "tokens_in" not in outcome, name
