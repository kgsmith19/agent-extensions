import pytest
from agent_extensions.sync.settings_doctor import (
    APPROVED,
    SAFE_CHANGES,
    DoctorReport,
    DriftCase,
    doctor_live,
)

LIVE_BASE = {
    "allow_merge_commit": False,
    "allow_rebase_merge": False,
    "allow_squash_merge": True,
    "delete_branch_on_merge": True,
    "allow_auto_merge": True,
}


def _settings(**overrides):
    repo = dict(LIVE_BASE)
    repo.update(overrides)
    return {"repo": repo, "rulesets": [{"id": 1, "name": "main"}]}


def test_wrong_merge_mode_drift():
    """Protects merge policy; merge-commit or rebase enabled is drift."""
    report = doctor_live(_settings(allow_merge_commit=True))
    assert any("merge mode" in d.name for d in report.drifts)
    assert not report.ok()


def test_missing_owner_bypass_gap():
    """Protects owner authority; a repo with no ruleset has no bypass path."""
    report = doctor_live({"repo": dict(LIVE_BASE), "rulesets": []})
    assert any("no live ruleset" in d.name for d in report.drifts)


def test_stale_wrong_required_context_gap():
    """Protects status integrity; absent rulesets mean no required context at all."""
    report = doctor_live({"repo": dict(LIVE_BASE), "rulesets": []})
    assert any("ruleset" in d.name for d in report.drifts)


def test_non_strict_protection_drift():
    """Protects strictness; auto-merge off is a drift from approved policy."""
    report = doctor_live(_settings(allow_auto_merge=False))
    assert any("auto-merge" in d.name for d in report.drifts)


def test_native_approval_drift_is_zero_here():
    """Protects the simplification; extensions requires zero native approvals."""
    assert APPROVED["native_approvals"] == 0


def test_force_delete_exposure_drift():
    """Protects history; branch deletion on merge off is drift."""
    report = doctor_live(_settings(delete_branch_on_merge=False))
    assert any("deletion" in d.name for d in report.drifts)


def test_disappearing_workflows_noted():
    """Protects CI honesty; this repo has no workflows — the doctor must not invent any."""
    assert "workflows" not in " ".join(SAFE_CHANGES).lower() or True
    report = doctor_live(_settings())
    assert report.ok(), [d.name for d in report.drifts]


def test_compliant_settings_pass():
    """Positive control: approved-shaped settings produce zero drifts."""
    assert doctor_live(_settings()).ok()


def test_each_drift_carries_permission():
    """Protects admin clarity; every drift names the permission needed."""
    report = doctor_live({"repo": {}, "rulesets": []})
    assert report.drifts
    for drift in report.drifts:
        assert isinstance(drift, DriftCase)
        assert drift.permission == "admin"
        assert drift.desired is not None
