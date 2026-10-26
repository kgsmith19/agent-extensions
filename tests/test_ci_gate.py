"""CI gate scripts (.github/ci/*) — Issue #64.

The PR Gate is the only required check on main, so its own logic is
tested like product code: the aggregator must fail on anything but
exact success, the script-test runner must never silently drop a test,
and the policy check must reject PRs that cannot be traced to an Issue.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
CI = ROOT / ".github" / "ci"


def _load(name):
    spec = importlib.util.spec_from_file_location("ci_" + name, CI / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    sys.modules["ci_" + name] = module
    spec.loader.exec_module(module)
    return module


gate = _load("gate")
script_tests = _load("script_tests")
policy = _load("policy")

JOBS = {"policy", "pytest", "shell", "pwsh", "hygiene"}


def _needs(**overrides):
    needs = {job: {"result": "success"} for job in JOBS}
    for job, result in overrides.items():
        if result is None:
            needs.pop(job)
        else:
            needs[job] = {"result": result}
    return needs


# --- gate aggregator (AC3) -------------------------------------------------


def test_gate_passes_only_when_every_job_succeeded():
    assert gate.evaluate(_needs(), JOBS) == []


@pytest.mark.parametrize("result", ["failure", "cancelled", "skipped", "neutral", ""])
def test_gate_fails_on_any_non_success(result):
    problems = gate.evaluate(_needs(pytest=result), JOBS)
    assert problems and "pytest" in problems[0]


def test_gate_fails_when_an_expected_job_is_missing():
    problems = gate.evaluate(_needs(pwsh=None), JOBS)
    assert any("pwsh" in p for p in problems)


def test_gate_fails_when_an_unexpected_job_appears():
    needs = _needs()
    needs["surprise"] = {"result": "success"}
    assert any("surprise" in p for p in gate.evaluate(needs, JOBS))


def test_gate_main_exit_code(monkeypatch, capsys):
    import json

    monkeypatch.setenv("NEEDS", json.dumps(_needs(shell="skipped")))
    monkeypatch.setenv("EXPECTED_JOBS", " ".join(sorted(JOBS)))
    assert gate.main() == 1
    monkeypatch.setenv("NEEDS", json.dumps(_needs()))
    assert gate.main() == 0


# --- script-test discovery (AC2) -------------------------------------------


def _tree(tmp_path, names):
    (tmp_path / "bootstrap").mkdir()
    for name in names:
        (tmp_path / "bootstrap" / name).write_text("exit 0\n")
    return tmp_path


def test_discovery_includes_every_test_by_default(tmp_path):
    root = _tree(tmp_path, ["a.test.sh", "b.test.sh", "c.test.ps1", "sync.sh"])
    run, excluded = script_tests.discover(root, "sh", live_only={})
    assert [p.name for p in run] == ["a.test.sh", "b.test.sh"]
    assert excluded == []


def test_discovery_excludes_only_named_live_scripts(tmp_path):
    root = _tree(tmp_path, ["a.test.ps1", "live.test.ps1"])
    live = {"bootstrap/live.test.ps1": "drives the real claude CLI"}
    run, excluded = script_tests.discover(root, "ps1", live_only=live)
    assert [p.name for p in run] == ["a.test.ps1"]
    assert excluded == [("bootstrap/live.test.ps1", "drives the real claude CLI")]


def test_stale_live_exclusion_is_an_error(tmp_path):
    root = _tree(tmp_path, ["a.test.ps1"])
    with pytest.raises(script_tests.StaleExclusion):
        script_tests.discover(root, "ps1", live_only={"bootstrap/gone.test.ps1": "x"})


def test_live_exclusion_requires_a_reason(tmp_path):
    root = _tree(tmp_path, ["live.test.ps1"])
    with pytest.raises(script_tests.StaleExclusion):
        script_tests.discover(root, "ps1", live_only={"bootstrap/live.test.ps1": " "})


def test_repository_live_list_matches_real_files():
    run, excluded = script_tests.discover(ROOT, "ps1")
    assert excluded, "the live-only list should name sync.claude-code.test.ps1"
    assert all(reason.strip() for _, reason in excluded)
    assert run, "hermetic ps1 tests must still run"


def test_runner_reports_failure_of_any_script(tmp_path):
    root = _tree(tmp_path, ["ok.test.sh"])
    (root / "bootstrap" / "bad.test.sh").write_text("echo boom; exit 3\n")
    results = script_tests.run_all(root, "sh", live_only={})
    assert {r.name: r.code for r in results} == {"bad.test.sh": 3, "ok.test.sh": 0}
    assert script_tests.summarize(results) == 1


def test_runner_fails_when_nothing_was_discovered(tmp_path):
    """A glob or path bug that finds zero scripts must not read as green."""
    root = _tree(tmp_path, [])
    assert script_tests.summarize(script_tests.run_all(root, "sh", live_only={})) == 1


# --- PR policy (AC4) -------------------------------------------------------


@pytest.mark.parametrize(
    "body",
    ["Closes #64", "## Related\n\nFixes #12\n", "resolves #3 and more"],
)
def test_policy_accepts_closing_keyword_and_issue_branch(body):
    assert policy.check(body, "issue/64-ci-pr-gate") == []


@pytest.mark.parametrize("body", ["", "Related to #64", "Closes #", "closes#64x"])
def test_policy_rejects_body_without_closing_keyword(body):
    assert any("closing keyword" in p for p in policy.check(body, "issue/64-x"))


@pytest.mark.parametrize("ref", ["fix/thing", "issue/64", "issue/x-y", "issue/64-Bad", "main"])
def test_policy_rejects_non_issue_branch(ref):
    assert any("issue/<n>-<slug>" in p for p in policy.check("Closes #64", ref))


# --- declared dependencies (AC1) -------------------------------------------


def test_dev_requirements_pin_the_declared_simplifier_version():
    """CI must execute the ruff version the simplifier pilot declares."""
    from agent_extensions.sync.simplifier_pilot import SIMPLIFIER_VERSION

    pins = dict(
        line.split("==", 1)
        for line in (ROOT / "requirements-dev.txt").read_text().splitlines()
        if "==" in line and not line.startswith("#")
    )
    assert pins["ruff"] == SIMPLIFIER_VERSION
    assert pins["pydantic"].startswith("2."), "schemas use the pydantic v2 API"


def test_gate_needs_every_job_and_expects_exactly_those():
    """A job added to the workflow but not gated would run without effect."""
    import re

    text = (ROOT / ".github" / "workflows" / "pr-gate.yml").read_text()
    jobs_block = text.split("\njobs:\n", 1)[1]
    jobs = set(re.findall(r"(?m)^  ([a-z][a-z0-9_-]*):\s*$", jobs_block)) - {"gate"}
    needs = set(re.search(r"needs: \[([^\]]*)\]", jobs_block).group(1).replace(" ", "").split(","))
    expected = set(re.search(r"EXPECTED_JOBS: (.+)", jobs_block).group(1).split())
    assert jobs == needs == expected, (jobs, needs, expected)
