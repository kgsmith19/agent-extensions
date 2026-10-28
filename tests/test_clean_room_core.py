"""Clean-room benchmark core contract (#91): std pin, prompt, acceptance, trials, gaps, runner.

Hermetic: the pinned std tree comes from `git archive`; no network. The runner
tests use fake CLIs on PATH (tests/fixtures/clean_room/fake_clis.py).
"""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from tests.fixtures.clean_room.fake_clis import (
    ACCEPTANCE,
    FAKE_CLIS,
    write_fake_cli,
)
from tests.fixtures.clean_room.std_tree import (  # noqa: F401  (fixtures)
    PINNED_FILES,
    SCHEMA_RELPATH,
    std_module,
    std_tree,
)

from canary.benchmark import core  # noqa: E402
from canary.benchmark import report as rpt  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[1]
LIVE_LANE = REPO_ROOT / "canary" / "benchmark" / "live-lane.json"
PR_IDS = [
    "cr.R0.spec", "cr.R1.spec", "cr.R2.spec", "cr.R3.spec",
    "cr.adv.stale-or-corrupt-event", "cr.adv.unsupported-schema",
]


def _entry(corpus, checkpoint_id):
    for entry in corpus["checkpoints"] + corpus["adversarial"]:
        if entry["id"] == checkpoint_id:
            return entry
    raise AssertionError("no checkpoint named %s in the pinned corpus" % checkpoint_id)


def _oracle(handle, std_module, checkpoint_id="cr.R0.spec"):
    return std_module.oracle_trial(_entry(handle.corpus, checkpoint_id),
                                   harness="fake-harness",
                                   provider="fake-provider",
                                   model_alias="fake/m1")


def _block(fields):
    """A transcript carrying one fenced acceptance block with the given fields."""
    return "Restating first.\n```acceptance\n" + json.dumps(fields) + "\n```\n"


def _checkpoint(handle, checkpoint_id="cr.R2.green"):
    return _entry(handle.corpus, checkpoint_id)


# --- std pin --------------------------------------------------------------------


def test_load_std_on_the_pinned_tree_succeeds(std_tree):
    handle = core.load_std(std_tree)
    assert len(handle.corpus["checkpoints"]) == 28
    assert len(handle.corpus["adversarial"]) == 12
    schema = json.loads((std_tree / SCHEMA_RELPATH).read_text(encoding="utf-8"))
    assert handle.schema == schema


@pytest.mark.parametrize("relpath", PINNED_FILES)
def test_one_flipped_byte_in_a_pinned_std_file_raises_pin_mismatch(std_tree, tmp_path, relpath):
    tree = tmp_path / "std"
    shutil.copytree(std_tree, tree)
    target = tree / relpath
    data = target.read_bytes()
    target.write_bytes(data[:-1] + bytes([data[-1] ^ 0x01]))
    with pytest.raises(core.StdPinMismatch):
        core.load_std(tree)


# --- trial schema: drift and validation -----------------------------------------


def test_trial_model_is_strict_and_mirrors_the_pinned_schema(std_tree):
    schema = json.loads((std_tree / SCHEMA_RELPATH).read_text(encoding="utf-8"))
    assert core.TrialModel.model_config.get("extra") == "forbid"
    fields = core.TrialModel.model_fields
    required = {name for name, field in fields.items() if field.is_required()}
    optional = set(fields) - required
    assert required == set(schema["required"])
    assert optional == set(schema["properties"]) - set(schema["required"])
    assert set(fields) == set(schema["properties"])


@pytest.mark.parametrize("checkpoint_id", ["cr.R0.spec", "cr.R3.merge-release", "cr.adv.crash-after-effect"])
def test_validate_trial_accepts_the_std_oracle_trial(std_tree, std_module, checkpoint_id):
    handle = core.load_std(std_tree)
    core.validate_trial(_oracle(handle, std_module, checkpoint_id))


def test_validate_trial_rejects_an_extra_field(std_tree, std_module):
    trial = _oracle(core.load_std(std_tree), std_module)
    trial["surprise"] = "not in the schema"
    with pytest.raises(ValueError):
        core.validate_trial(trial)


def test_validate_trial_rejects_a_missing_harness(std_tree, std_module):
    trial = _oracle(core.load_std(std_tree), std_module)
    del trial["harness"]
    with pytest.raises(ValueError):
        core.validate_trial(trial)


@pytest.mark.parametrize("bad_id", ["cr.R9.spec", "cr.R0.Spec", "spec", "cr.adv.Unknown_Kind"])
def test_validate_trial_rejects_a_bad_checkpoint_id_pattern(std_tree, std_module, bad_id):
    trial = _oracle(core.load_std(std_tree), std_module)
    trial["checkpoint_id"] = bad_id
    with pytest.raises(ValueError):
        core.validate_trial(trial)


@pytest.mark.parametrize("bad_digest", [
    "sha256:not-a-digest",
    "sha256:" + "A" * 64,  # uppercase hex is not the schema's lowercase form
    "sha256:" + "a" * 63,
])
def test_validate_trial_rejects_a_bad_raw_log_digest(std_tree, std_module, bad_digest):
    trial = _oracle(core.load_std(std_tree), std_module)
    trial["raw_log_digest"] = bad_digest
    with pytest.raises(ValueError):
        core.validate_trial(trial)


# --- D1: the prompt ---------------------------------------------------------------


def test_build_prompt_carries_prompt_text_verbatim_then_handoff_then_protocol(std_tree):
    checkpoint = _checkpoint(core.load_std(std_tree))
    prompt = core.build_prompt(checkpoint)
    fixture = checkpoint["fixture"]
    assert prompt.startswith(fixture["prompt_text"])
    assert fixture["prompt_text"] in prompt
    for field in ("issue", "branch", "base_sha", "head_sha"):
        assert fixture[field] in prompt, field
    handoff = prompt.index(fixture["branch"])
    protocol = prompt.index("```acceptance")
    assert fixture["prompt_text"] in prompt[:handoff]
    assert handoff < protocol
    assert "before any other action" in prompt[protocol - 200:protocol + 200].lower()


def test_build_prompt_refuses_a_checkpoint_without_prompt_text():
    checkpoint = {"id": "cr.R0.spec", "fixture": {"head_sha": "a" * 40}}
    with pytest.raises(ValueError, match="prompt_text"):
        core.build_prompt(checkpoint)


def test_build_prompt_refuses_blank_prompt_text():
    checkpoint = {"id": "cr.R0.spec", "fixture": {"prompt_text": "   "}}
    with pytest.raises(ValueError, match="prompt_text"):
        core.build_prompt(checkpoint)


# --- D2: acceptance attempt parsing ------------------------------------------------


def test_parse_acceptance_reads_a_valid_block_and_sets_the_prompt_sent():
    attempt = core.parse_acceptance(_block(ACCEPTANCE), "the prompt sent")
    assert attempt["role"] == "builder"
    assert attempt["stated_phase"] == "spec"
    assert attempt["stated_rules"] == ["route.R0"]
    assert attempt["restatement"] == "draft-spec:thin-spec"
    assert attempt["prompt_text"] == "the prompt sent"


def test_parse_acceptance_with_no_block_yields_empty_fields_not_a_fake():
    attempt = core.parse_acceptance("I will just do it.", "the prompt sent")
    assert attempt == {
        "role": "", "stated_phase": "", "stated_head": "", "stated_rules": [],
        "accepted_scope": [], "prompt_text": "the prompt sent", "restatement": "",
    }


def test_parse_acceptance_with_malformed_json_yields_empty_fields():
    attempt = core.parse_acceptance("```acceptance\n{not json\n```", "p")
    assert attempt["role"] == "" and attempt["stated_rules"] == []
    assert attempt["prompt_text"] == "p"


def test_parse_acceptance_with_two_blocks_is_malformed_not_first_wins():
    text = _block(ACCEPTANCE) + _block(dict(ACCEPTANCE, role="someone-else"))
    attempt = core.parse_acceptance(text, "p")
    assert attempt["role"] == ""


def test_parse_acceptance_with_a_block_missing_a_claimed_field_is_malformed():
    partial = {key: value for key, value in ACCEPTANCE.items() if key != "restatement"}
    attempt = core.parse_acceptance(_block(partial), "p")
    assert attempt["role"] == "" and attempt["restatement"] == ""


# --- run_trial and the Driver protocol ------------------------------------------


class FakeDriver:
    name = "fake-harness"
    exe = "fake-cli"

    def __init__(self, trial=None, gap_list=(), transcript=None, collect_error=None,
                 run_error=None):
        self.calls = []
        self._trial = trial
        self._gaps = list(gap_list)
        self._transcript = transcript
        self._collect_error = collect_error
        self._run_error = run_error

    def prepare(self, checkpoint, clean_home):
        self.calls.append(("prepare", checkpoint["id"], Path(clean_home)))

    def run(self, model):
        self.calls.append(("run", model))
        if self._run_error is not None:
            raise self._run_error

    def collect(self):
        self.calls.append(("collect",))
        if self._collect_error is not None:
            raise self._collect_error
        return dict(self._trial, transcript_text=self._transcript)

    def gaps(self):
        return list(self._gaps)


def _accepting_transcript(handle, checkpoint_id="cr.R2.green", std_module=None):
    return _block({key: value for key, value in _oracle(handle, std_module, checkpoint_id)[
        "acceptance_attempt"].items() if key in core.AcceptanceAttempt.model_fields})


def test_run_trial_calls_prepare_run_and_collect_once_each_in_order(std_tree, std_module, tmp_path):
    handle = core.load_std(std_tree)
    checkpoint = _checkpoint(handle)
    trial = _oracle(handle, std_module, "cr.R2.green")
    driver = FakeDriver(trial=trial, transcript=_accepting_transcript(handle, std_module=std_module))
    clean_home = tmp_path / "home"

    result = core.run_trial(driver, checkpoint, "fake/m1", clean_home)

    assert driver.calls == [
        ("prepare", "cr.R2.green", clean_home),
        ("run", "fake/m1"),
        ("collect",),
    ]
    assert "transcript_text" not in result
    assert result["acceptance_attempt"] == core.parse_acceptance(
        _accepting_transcript(handle, std_module=std_module), core.build_prompt(checkpoint))
    core.validate_trial(result)


def test_run_trial_fills_the_acceptance_attempt_from_the_transcript_not_the_driver(
        std_tree, std_module, tmp_path):
    handle = core.load_std(std_tree)
    checkpoint = _checkpoint(handle)
    trial = _oracle(handle, std_module, "cr.R2.green")
    trial["acceptance_attempt"] = {"role": "forged"}  # a driver claim must not survive
    driver = FakeDriver(trial=trial, transcript="no block here")
    result = core.run_trial(driver, checkpoint, "fake/m1", tmp_path / "home")
    assert result["acceptance_attempt"]["role"] == ""
    assert result["acceptance_attempt"]["prompt_text"] == core.build_prompt(checkpoint)


def test_run_trial_propagates_a_schema_error_from_a_bad_collect(std_tree, std_module, tmp_path):
    handle = core.load_std(std_tree)
    checkpoint = _checkpoint(handle)
    bad = _oracle(handle, std_module, "cr.R2.green")
    bad["tokens_in"] = -1  # schema: minimum 0
    driver = FakeDriver(trial=bad, transcript=_accepting_transcript(handle, std_module=std_module))
    with pytest.raises(ValueError):
        core.run_trial(driver, checkpoint, "fake/m1", tmp_path / "home")


# --- D6: failed or incomplete runs are gaps, never trials ----------------------------


def test_a_non_zero_exit_becomes_a_cli_run_failed_gap_not_a_trial(std_tree, tmp_path):
    handle = core.load_std(std_tree)
    driver = FakeDriver(run_error=core.CliRunFailed("fake-cli", 2, "boom"))
    result = core.run_trial(driver, _checkpoint(handle), "fake/m1", tmp_path / "home")
    assert result == {"status": "gap", "gap": {
        "id": "cli-run-failed", "reason": "fake-cli exited 2: boom"}}
    assert ("collect",) not in driver.calls


def test_a_run_without_a_terminal_event_becomes_a_gap(std_tree, tmp_path):
    handle = core.load_std(std_tree)
    driver = FakeDriver(collect_error=core.RunIncomplete("no result event"),
                        transcript="x")
    result = core.run_trial(driver, _checkpoint(handle), "fake/m1", tmp_path / "home")
    assert result["status"] == "gap"
    assert result["gap"]["id"] == "cli-run-failed"
    assert "no result event" in result["gap"]["reason"]


def test_a_missing_cli_becomes_a_cli_not_installed_gap(std_tree, tmp_path):
    handle = core.load_std(std_tree)
    driver = FakeDriver(run_error=core.CliNotInstalled("fake-cli"))
    result = core.run_trial(driver, _checkpoint(handle), "fake/m1", tmp_path / "home")
    assert result["status"] == "gap"
    assert result["gap"]["id"] == "cli-not-installed"


def test_redaction_removes_api_key_shapes_from_a_gap_reason(std_tree, tmp_path):
    handle = core.load_std(std_tree)
    leaked = "sk-proj-SECRETVALUE1234567890 and AIzaSyD-EXAMPLEKEY_1234567890abc"
    driver = FakeDriver(run_error=core.CliRunFailed("fake-cli", 1, leaked))
    result = core.run_trial(driver, _checkpoint(handle), "fake/m1", tmp_path / "home")
    reason = result["gap"]["reason"]
    assert "SECRETVALUE" not in reason and "EXAMPLEKEY" not in reason
    assert reason.count("[REDACTED]") == 2
    assert core.redact("plain text") == "plain text"


# --- status records and gaps -----------------------------------------------------


def test_not_provisioned_status_record_shape():
    assert core.not_provisioned("kilo") == {
        "provider": "kilo",
        "status": "skipped: not provisioned",
    }


def test_each_driver_gap_is_reported_as_id_and_reason():
    driver = FakeDriver(gap_list=[
        {"id": "hooks-not-run", "reason": "headless run uses --bare"},
        {"id": "sandbox", "reason": "no sandbox flag in headless mode"},
    ])
    assert core.gap_records(driver) == [
        {"id": "hooks-not-run", "reason": "headless run uses --bare"},
        {"id": "sandbox", "reason": "no sandbox flag in headless mode"},
    ]


def test_a_driver_gap_without_a_reason_is_refused_not_faked():
    driver = FakeDriver(gap_list=[{"id": "hooks-not-run", "reason": ""}])
    with pytest.raises(ValueError):
        core.gap_records(driver)


# --- D8: the runner entry point ------------------------------------------------------


def test_pr_subset_is_first_of_each_tier_plus_two_adversarial(std_tree):
    corpus = core.load_std(std_tree).corpus
    assert core.select_checkpoints(corpus, "pr") == PR_IDS


def test_all_subset_is_every_checkpoint_in_corpus_order(std_tree):
    corpus = core.load_std(std_tree).corpus
    expected = [e["id"] for e in corpus["checkpoints"] + corpus["adversarial"]]
    assert core.select_checkpoints(corpus, "all") == expected
    assert len(expected) == 40


def test_live_records_are_not_provisioned_unless_flag_and_identity_are_both_set():
    lane = {"infisical_identity_id": "", "providers": {
        "kilo": {"provisioned": True}, "codex": {"provisioned": False}}}
    assert core.live_records(lane, ["kilo", "codex"]) == [
        {"provider": "kilo", "status": "skipped: not provisioned"},
        {"provider": "codex", "status": "skipped: not provisioned"},
    ]


def test_the_live_lane_file_has_exactly_the_amended_shape():
    lane = json.loads(LIVE_LANE.read_text(encoding="utf-8"))
    assert lane == {
        "infisical_identity_id": "",
        "project_slug": "",
        "secret_path": "",
        "providers": {
            "claude-code": {"provisioned": False},
            "codex": {"provisioned": False},
            "gemini-cli": {"provisioned": False},
            "kilo": {"provisioned": False},
        },
    }


def test_each_cli_gets_a_model_alias_its_wire_route_accepts():
    from canary.benchmark import __main__ as runner

    # gemini's route is /v1beta/models/{m}:streamGenerateContent, so its alias is one segment;
    # kilo needs the provider-prefixed alias its config renders.
    assert "/" not in runner.scripted_alias("gemini-cli")
    assert runner.scripted_alias("kilo") == "scripted/m1"
    assert runner.scripted_alias("claude-code") == runner.scripted_alias("codex") == "m1"


def _run_runner(args, path_dirs, cwd=REPO_ROOT):
    env = {"PATH": os.pathsep.join([*map(str, path_dirs), "/usr/bin", "/bin"])}
    return subprocess.run([sys.executable, "-m", "canary.benchmark", *args],
                          cwd=cwd, env=env, capture_output=True, text=True, timeout=600,
                          check=False)


def _fake_bin(tmp_path, names):
    bin_dir = tmp_path / "fake-bin"
    for name in names:
        exe, canned, _, _ = FAKE_CLIS[name]
        write_fake_cli(bin_dir, exe, canned)
    return bin_dir


def test_deterministic_pr_run_writes_three_outputs_and_gaps_for_a_missing_cli(std_tree, tmp_path):
    bin_dir = _fake_bin(tmp_path, ["claude-code", "kilo"])
    out = tmp_path / "out"
    assert shutil.which("codex", path=f"{bin_dir}:/usr/bin:/bin") is None
    result = _run_runner(["--lane", "deterministic", "--subset", "pr", "--std-dir", str(std_tree),
                          "--out", str(out), "--clis", "claude-code,kilo,codex"], [bin_dir])
    assert result.returncode == 0, result.stderr

    trials = json.loads((out / "trials.json").read_text(encoding="utf-8"))
    assert len(trials) == 2 * len(PR_IDS)
    assert sorted({t["harness"] for t in trials}) == ["claude-code", "kilo"]
    assert [t["checkpoint_id"] for t in trials if t["harness"] == "kilo"] == PR_IDS

    report = json.loads((out / "report.json").read_text(encoding="utf-8"))
    assert report["trials"] == len(trials)
    assert any(record.get("id") == "cli-not-installed" and record.get("harness") == "codex"
               for record in report["status"]), report["status"]
    markdown = (out / "report.md").read_text(encoding="utf-8")
    assert all(checkpoint_id in markdown for checkpoint_id in PR_IDS)


def test_live_run_records_every_unprovisioned_provider_and_runs_no_trials(std_tree, tmp_path):
    out = tmp_path / "out"
    result = _run_runner(["--lane", "live", "--subset", "pr", "--std-dir", str(std_tree),
                          "--out", str(out),
                          "--clis", "claude-code,codex,gemini-cli,kilo"], [])
    assert result.returncode == 0, result.stderr
    assert json.loads((out / "trials.json").read_text(encoding="utf-8")) == []
    report = json.loads((out / "report.json").read_text(encoding="utf-8"))
    assert report["status"] == [
        {"provider": name, "status": "skipped: not provisioned"}
        for name in ("claude-code", "codex", "gemini-cli", "kilo")
    ]
    assert (out / "report.md").is_file()


def test_a_standard_pin_mismatch_exits_non_zero(std_tree, tmp_path):
    tree = tmp_path / "std"
    shutil.copytree(std_tree, tree)
    target = tree / "tools" / "handoff_acceptance.py"
    target.write_bytes(target.read_bytes() + b"\n# tampered\n")
    result = _run_runner(["--lane", "live", "--subset", "pr", "--std-dir", str(tree),
                          "--out", str(tmp_path / "out")], [])
    assert result.returncode != 0
    assert "handoff_acceptance.py" in result.stderr


def test_a_crash_inside_a_run_exits_non_zero(std_tree, tmp_path):
    bin_dir = tmp_path / "crash-bin"
    write_fake_cli(bin_dir, "claude", b"this is not json\n")
    result = _run_runner(["--lane", "deterministic", "--subset", "pr", "--std-dir", str(std_tree),
                          "--out", str(tmp_path / "out"), "--clis", "claude-code"], [bin_dir])
    assert result.returncode != 0
    assert "ValueError: line 1: not JSON" in result.stderr, result.stderr
