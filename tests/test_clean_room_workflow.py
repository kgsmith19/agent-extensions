"""Clean-room benchmark workflow shape (#91 AC3/AC4), read as text like test_kilo_canary.py.

RED: .github/workflows/clean-room-benchmark.yml is orchestrator-owned and not written
yet, so each test fails on the existence assertion with a clear message.
"""

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
WORKFLOW = REPO / ".github" / "workflows" / "clean-room-benchmark.yml"
MISSING = "missing .github/workflows/clean-room-benchmark.yml (#91 AC3/AC4)"


def _text():
    assert WORKFLOW.exists(), MISSING
    return WORKFLOW.read_text(encoding="utf-8")


def _split(text):
    """Return (head before `jobs:`, {job name: job block text})."""
    assert "\njobs:\n" in text, "workflow has no top-level jobs: block"
    head, jobs_text = text.split("\njobs:\n", 1)
    matches = list(re.finditer(r"^  ([A-Za-z][\w-]*):\s*$", jobs_text, re.M))
    assert matches, "no jobs declared under jobs:"
    jobs = {}
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(jobs_text)
        jobs[match.group(1)] = jobs_text[match.end():end]
    return head, jobs


def _job(jobs, name):
    assert name in jobs, "workflow has no %r job (have: %s)" % (name, sorted(jobs))
    return jobs[name]


def _upload_steps(text):
    """Step blocks (split at each `      - ` step marker) that use upload-artifact."""
    chunks = re.split(r"(?m)^      - ", text)
    return [chunk for chunk in chunks if "upload-artifact@" in chunk]


def test_triggers_are_pull_request_on_canary_benchmark_and_dispatch_with_lane():
    head, _jobs = _split(_text())
    assert "pull_request:" in head
    assert "workflow_dispatch:" in head
    pull_request_block = head.split("pull_request:", 1)[1].split("workflow_dispatch:", 1)[0]
    assert "canary/benchmark/**" in pull_request_block
    dispatch_block = head.split("workflow_dispatch:", 1)[1]
    assert "lane:" in dispatch_block
    lane_block = dispatch_block.split("lane:", 1)[1]
    options = re.findall(r"^\s+- ([\w-]+)\s*$", lane_block, re.M)
    assert options == ["deterministic", "live"], options


def test_live_job_runs_only_on_workflow_dispatch_with_lane_live():
    _head, jobs = _split(_text())
    live = _job(jobs, "live")
    assert "if: github.event_name == 'workflow_dispatch' && inputs.lane == 'live'" in live
    for name, body in jobs.items():
        if name != "live":
            assert "inputs.lane == 'live'" not in body, "job %r must not run the live lane" % name


def test_live_job_reads_its_secret_through_infisical_oidc():
    _head, jobs = _split(_text())
    live = _job(jobs, "live")
    assert "Infisical/secrets-action@" in live
    assert "method: oidc" in live
    for name, body in jobs.items():
        if name != "live":
            assert "Infisical" not in body, "job %r must not reach Infisical" % name


def test_every_uses_reference_is_pinned_to_a_full_40_hex_sha():
    uses = re.findall(r"uses:\s*(\S+)", _text())
    assert uses, "workflow has no uses: references"
    for reference in uses:
        assert re.fullmatch(r".+@[0-9a-f]{40}", reference), reference


def test_upload_steps_fail_closed_and_upload_trials_report_json_and_report_md():
    steps = _upload_steps(_text())
    assert steps, "no actions/upload-artifact step"
    for step in steps:
        assert "if-no-files-found: error" in step, step
    uploaded = " ".join(steps)
    for name in ("trials.json", "report.json", "report.md"):
        assert name in uploaded, "%s is not uploaded as an artifact" % name


def test_top_level_permissions_are_contents_read_only():
    text = _text()
    head, _jobs = _split(text)
    assert re.search(r"(?m)^permissions:\n  contents: read\s*$", head), head
    assert "contents: write" not in text


def test_id_token_write_appears_only_in_the_live_job():
    text = _text()
    head, jobs = _split(text)
    assert "id-token" not in head
    assert text.count("id-token: write") == 1
    assert "id-token: write" in _job(jobs, "live")
    for name, body in jobs.items():
        if name != "live":
            assert "id-token" not in body, "job %r must not request an OIDC token" % name
