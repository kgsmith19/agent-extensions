"""Stage 3b doctor: drift fixtures + live read-back for GitHub settings."""

import json
import subprocess
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

# Owner-approved policy (mirrors the Standard repo's live ruleset 20904938,
# minus the Standard-specific required-status name which extensions does not share).
APPROVED = {
    "allow_merge_commit": False,
    "allow_rebase_merge": False,
    "allow_squash_merge": True,
    "delete_branch_on_merge": True,
    "allow_auto_merge": True,
    "linear_history": True,
    "no_force_delete": True,
    "native_approvals": 0,
    "owner_bypass": True,
    "strict_up_to_date": True,
}


@dataclass
class DriftCase:
    """One drift mode: what it looks like live vs what policy demands."""

    name: str
    current: Any
    desired: Any
    permission: str = "admin"


@dataclass
class DoctorReport:
    """Full read-back: drifts found, each with current/desired/permission."""

    drifts: List[DriftCase] = field(default_factory=list)

    def ok(self) -> bool:
        return not self.drifts


def _api(path: str, method: str = "GET", **kwargs) -> Any:
    cmd = ["gh", "api", path]
    if method != "GET":
        cmd += ["--method", method]
    for key, value in kwargs.items():
        cmd += ["-f", f"{key}={value}"]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"gh api {path} failed: {proc.stderr.strip()}")
    return json.loads(proc.stdout) if proc.stdout.strip() else {}


def read_live_settings(repo: str = "kgsmith19/agent-extensions") -> Dict[str, Any]:
    """Read every governed value back from the live API (never trust templates)."""
    data = _api(f"repos/{repo}")
    try:
        rulesets = _api(f"repos/{repo}/rulesets") or []
    except RuntimeError:
        rulesets = []
    return {"repo": data, "rulesets": rulesets}


def doctor_live(settings: Dict[str, Any]) -> DoctorReport:
    """Compare live read-back against APPROVED; every drift named explicitly."""
    repo = settings["repo"]
    report = DoctorReport()
    for key in ("allow_merge_commit", "allow_rebase_merge"):
        if repo.get(key) is not False:
            report.drifts.append(DriftCase(
                f"wrong merge mode: {key}", repo.get(key), False))
    if repo.get("allow_squash_merge") is not True:
        report.drifts.append(DriftCase(
            "squash merge disabled", repo.get("allow_squash_merge"), True))
    if repo.get("delete_branch_on_merge") is not True:
        report.drifts.append(DriftCase(
            "branch deletion on merge off", repo.get("delete_branch_on_merge"), True))
    if repo.get("allow_auto_merge") is not True:
        report.drifts.append(DriftCase(
            "auto-merge disabled", repo.get("allow_auto_merge"), True))
    if not settings.get("rulesets"):
        report.drifts.append(DriftCase(
            "no live ruleset on main", "branch not protected / no rulesets",
            "active ruleset: deletion guard, squash-only, owner bypass, "
            "zero native approvals, strict status checks"))
    return report


SAFE_CHANGES = [
    "squash-only merge (disable merge-commit + rebase)",
    "enable auto-merge",
    "delete branch on merge",
    "linear history via squash-only ruleset",
    "no force-push / no delete (ruleset deletion guard)",
    "zero native approvals (no stable Gate name to preserve here)",
    "owner bypass (owner admin only, never delegated)",
    "strict up-to-date branch protection",
]
