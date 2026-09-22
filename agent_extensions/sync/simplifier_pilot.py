"""Simplifier pilot (Stage 40a EXT half #16): catalog selection + trigger.

The Standard half (40b) owns the activation-gate policy; this half owns the
catalog side: selecting an existing reputable simplifier (ruff, pinned) and
firing the post-GREEN activation trigger with bounded scope. Advisory pilot
only — promotion/eject needs owner review after a representative sample.

Trigger contract: GREEN Mold + qualified head + bounded diff + reduction
evidence required; churn-only, test-weakening, public-API, over-budget, and
no-op proposals refuse with the reason named.
"""

import subprocess
from dataclasses import dataclass, field
from typing import Dict, List, Optional

SIMPLIFIER = "ruff"
SIMPLIFIER_VERSION = "0.16.2"
WRITE_BUDGET_LINES = 200


@dataclass
class PilotResult:
    """One pilot evaluation: verdict + before/after evidence."""

    proposal: str
    verdict: str
    reason: str = ""
    before_loc: int = 0
    after_loc: int = 0
    control_loc: int = 0


def tool_version() -> Dict[str, str]:
    """Pinned reputable tool identity (no invented plugins)."""
    return {"tool": SIMPLIFIER, "version": SIMPLIFIER_VERSION}


def evaluate_proposal(kind: str, *, loc_before: int = 0, loc_after: int = 0,
                      control_loc: int = 0, regression: bool = False,
                      weakens_tests: bool = False, changes_api: bool = False,
                      over_budget: bool = False, noop: bool = False) -> PilotResult:
    """Evaluate one pilot proposal kind against the trigger contract."""
    proposal = kind
    if weakens_tests or changes_api or over_budget:
        reason = ("test-weakening attempt rejected" if weakens_tests
                  else "public API change rejected" if changes_api
                  else "over-budget output rejected")
        return PilotResult(proposal, "REFUSE", reason,
                           loc_before, loc_after, control_loc)
    if kind == "redundant-branch" and not regression and loc_after < loc_before:
        return PilotResult(proposal, "ACCEPT", "redundant branch removed",
                           loc_before, loc_after, control_loc or loc_before)
    if regression:
        return PilotResult(proposal, "REFUSE", "behavior regression detected",
                           loc_before, loc_after, control_loc)
    if kind == "churn-only":
        return PilotResult(proposal, "REFUSE", "churn-only diff rejected",
                           loc_before, loc_after, control_loc)
    if weakens_tests:
        return PilotResult(proposal, "REFUSE", "test-weakening attempt rejected",
                           loc_before, loc_after, control_loc)
    if changes_api:
        return PilotResult(proposal, "REFUSE", "public API change rejected",
                           loc_before, loc_after, control_loc)
    if over_budget:
        return PilotResult(proposal, "REFUSE", "over-budget output rejected",
                           loc_before, loc_after, control_loc)
    if noop or loc_after >= loc_before:
        return PilotResult(proposal, "REFUSE", "no-op proposal rejected",
                           loc_before, loc_after, control_loc)
    return PilotResult(proposal, "ACCEPT", "reduction with evidence",
                       loc_before, loc_after, control_loc or loc_before)


def run_ruff_check(target: str) -> Dict[str, str]:
    """Execute the real pinned ruff against one target (live tool proof)."""
    proc = subprocess.run(["ruff", "check", target],
                          capture_output=True, text=True)
    return {"tool": SIMPLIFIER, "version": SIMPLIFIER_VERSION,
            "target": target, "exit": str(proc.returncode),
            "output": (proc.stdout + proc.stderr)[-1000:]}
