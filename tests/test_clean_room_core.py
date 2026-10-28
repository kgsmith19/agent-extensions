"""Clean-room benchmark core contract (#91): std pin, trial schema, Driver, run_trial.

Hermetic: the pinned std tree comes from `git archive`; no network, no CLI.
RED: canary/benchmark/core.py does not exist yet, so the import below fails.
"""

import json
import shutil
from pathlib import Path

import pytest

from tests.fixtures.clean_room.std_tree import (  # noqa: F401  (fixtures)
    PINNED_FILES,
    SCHEMA_RELPATH,
    std_module,
    std_tree,
)

from canary.benchmark import core  # noqa: E402


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


# --- run_trial and the Driver protocol ------------------------------------------


class FakeDriver:
    name = "fake-harness"

    def __init__(self, trial=None, gap_list=()):
        self.calls = []
        self._trial = trial
        self._gaps = list(gap_list)

    def prepare(self, checkpoint, clean_home):
        self.calls.append(("prepare", checkpoint["id"], Path(clean_home)))

    def run(self, model):
        self.calls.append(("run", model))

    def collect(self):
        self.calls.append(("collect",))
        return self._trial

    def gaps(self):
        return list(self._gaps)


def test_run_trial_calls_prepare_run_and_collect_once_each_in_order(std_tree, std_module, tmp_path):
    handle = core.load_std(std_tree)
    checkpoint = _entry(handle.corpus, "cr.R2.green")
    trial = _oracle(handle, std_module, "cr.R2.green")
    driver = FakeDriver(trial=trial)
    clean_home = tmp_path / "home"

    result = core.run_trial(driver, checkpoint, "fake/m1", clean_home)

    assert driver.calls == [
        ("prepare", "cr.R2.green", clean_home),
        ("run", "fake/m1"),
        ("collect",),
    ]
    assert result == trial
    core.validate_trial(result)


def test_run_trial_propagates_a_schema_error_from_a_bad_collect(std_tree, std_module, tmp_path):
    handle = core.load_std(std_tree)
    checkpoint = _entry(handle.corpus, "cr.R2.green")
    bad = _oracle(handle, std_module, "cr.R2.green")
    bad["tokens_in"] = -1  # schema: minimum 0
    driver = FakeDriver(trial=bad)
    with pytest.raises(ValueError):
        core.run_trial(driver, checkpoint, "fake/m1", tmp_path / "home")


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
