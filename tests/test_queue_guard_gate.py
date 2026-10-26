"""Queue v2 guard in the agent-extensions PR Gate (#74; std#287 AC3).

Every PR must close only Issues on the ACTIVE item of the owner's execution
queue, unless the owner applied owner:queue-exempt. The rule is std's
`work_queue.py guard --pr-event`, reached through agent_extensions/queue_cli.py
(which fetches std's modules from main). These tests pin the step into the
required `policy` job, so it cannot be silently dropped, softened or made
conditional on anything but being a pull request.
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WORKFLOW = (ROOT / ".github" / "workflows" / "pr-gate.yml").read_text(encoding="utf-8")


def _job(name: str) -> str:
    start = WORKFLOW.index("\n  %s:\n" % name)
    rest = WORKFLOW[start + 1:]
    lines = rest.split("\n")
    out = [lines[0]]
    for line in lines[1:]:
        if line.startswith("  ") and not line.startswith("   ") and line.strip().endswith(":"):
            break
        out.append(line)
    return "\n".join(out)


def _guard_step() -> str:
    policy = _job("policy")
    marker = "- name: Work matches the execution queue"
    assert marker in policy, "guard step present in the required policy job"
    return policy.split(marker, 1)[1].split("\n      - name:", 1)[0]


def test_guard_step_runs_std_guard_through_queue_cli():
    """AC1: the step is std's guard, reached through the one shared fetch path, isolated."""
    step = _guard_step()
    assert 'python3 -I agent_extensions/queue_cli.py guard --pr-event "$GITHUB_EVENT_PATH"' in step
    assert "GITHUB_TOKEN: ${{ github.token }}" in step


def test_guard_step_is_hard_and_only_conditional_on_pull_requests():
    """AC1/AC2: a soft-failing or narrowly conditional guard would be no guard at all."""
    step = _guard_step()
    assert "continue-on-error" not in step
    conditions = [line.strip() for line in step.splitlines() if line.strip().startswith("if:")]
    assert conditions == ["if: github.event_name == 'pull_request'"]


def test_gate_reruns_when_the_exemption_label_changes():
    """AC1: adding or removing owner:queue-exempt must re-evaluate the gate."""
    on = WORKFLOW.split("\njobs:", 1)[0]
    types = on.split("types:", 1)[1].split("\n", 1)[0]
    for kind in ("opened", "synchronize", "edited", "labeled", "unlabeled"):
        assert kind in types, kind


def test_policy_job_can_read_the_pr_timeline():
    """AC1: owner provenance of the label comes from the PR timeline, which needs issues: read."""
    block = WORKFLOW.split("\npermissions:", 1)[1].split("\n\n", 1)[0]
    perms = {}
    for line in block.splitlines():
        line = line.split("#", 1)[0].strip()
        if line:
            key, _, value = line.partition(":")
            perms[key.strip()] = value.strip()
    assert perms.get("issues") == "read"
    assert "write" not in perms.values()
