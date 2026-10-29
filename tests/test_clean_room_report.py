"""Clean-room benchmark report (#91): build_report, write_report, status carry-through.

Hermetic. The independent oracle is the pinned std scorer's own report(),
loaded by path in the std_module fixture, not through canary.benchmark.
RED: canary/benchmark/report.py and core.py do not exist yet, so the import fails.
"""

import json
from pathlib import Path

from tests.fixtures.clean_room.std_tree import std_module, std_tree  # noqa: F401  (fixtures)

from canary.benchmark import core  # noqa: E402
from canary.benchmark import report as rpt  # noqa: E402


def _all_entries(corpus):
    return list(corpus["checkpoints"]) + list(corpus["adversarial"])


def _oracle_trials(std_module, corpus):
    return [std_module.oracle_trial(entry) for entry in _all_entries(corpus)]


def test_build_report_scores_equal_the_std_report_called_directly(std_tree, std_module):
    handle = core.load_std(std_tree)
    trials = _oracle_trials(std_module, handle.corpus)
    assert len(trials) == 40

    _markdown, std_json = std_module.report(trials, handle.corpus)
    expected = json.loads(std_json)

    got = rpt.build_report(trials, handle)

    assert {key: got[key] for key in expected} == expected
    assert got["trials"] == 40
    assert got["checkpoints"] == 28
    assert got["adversarial"] == 12
    assert expected["groups"], "std report produced no groups"
    assert all(group["success_rate"] == 1.0 for group in expected["groups"])


def test_write_report_writes_report_json_and_report_md(std_tree, std_module, tmp_path):
    handle = core.load_std(std_tree)
    report = rpt.build_report(_oracle_trials(std_module, handle.corpus), handle)
    out = tmp_path / "out"
    out.mkdir()

    paths = rpt.write_report(report, out)

    assert (out / "report.json").is_file()
    assert (out / "report.md").is_file()
    assert {Path(p).resolve() for p in paths} == {
        (out / "report.json").resolve(),
        (out / "report.md").resolve(),
    }


def test_report_json_round_trips(std_tree, std_module, tmp_path):
    handle = core.load_std(std_tree)
    report = rpt.build_report(_oracle_trials(std_module, handle.corpus), handle)
    out = tmp_path / "out"
    out.mkdir()

    rpt.write_report(report, out)

    loaded = json.loads((out / "report.json").read_text(encoding="utf-8"))
    assert loaded == report


def test_report_md_names_every_checkpoint_id(std_tree, std_module, tmp_path):
    handle = core.load_std(std_tree)
    report = rpt.build_report(_oracle_trials(std_module, handle.corpus), handle)
    out = tmp_path / "out"
    out.mkdir()

    rpt.write_report(report, out)

    markdown = (out / "report.md").read_text(encoding="utf-8")
    missing = [entry["id"] for entry in _all_entries(handle.corpus)
               if entry["id"] not in markdown]
    assert not missing, "report.md omits checkpoint ids: %s" % missing


def test_skipped_not_provisioned_record_is_carried_into_report_json(std_tree, std_module, tmp_path):
    handle = core.load_std(std_tree)
    trials = _oracle_trials(std_module, handle.corpus)
    record = core.not_provisioned("kilo")
    report = rpt.build_report(trials, handle, status=[record])
    out = tmp_path / "out"
    out.mkdir()

    rpt.write_report(report, out)

    loaded = json.loads((out / "report.json").read_text(encoding="utf-8"))
    assert loaded["status"] == [
        {"provider": "kilo", "status": "skipped: not provisioned"},
    ]
