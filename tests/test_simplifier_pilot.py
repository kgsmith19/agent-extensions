"""Stage 40a (EXT half #16): post-GREEN simplifier pilot, catalog + trigger."""

from agent_extensions.sync.simplifier_pilot import (
    WRITE_BUDGET_LINES,
    evaluate_proposal,
    run_ruff_check,
    tool_version,
)


def test_catalog_selection_names_pinned_tool():
    """Catalog selection is an existing reputable tool, pinned — never invented."""
    identity = tool_version()
    assert identity["tool"] == "ruff" and identity["version"]


def test_redundant_branch_accepted_with_evidence():
    """Correct simplification accepts with before/after LOC evidence."""
    result = evaluate_proposal("redundant-branch", loc_before=100, loc_after=70,
                               control_loc=100)
    assert result.verdict == "ACCEPT" and result.after_loc < result.before_loc


def test_behavior_regression_rejected():
    """A simplifying diff that regresses behavior refuses."""
    result = evaluate_proposal("redundant-branch", loc_before=100, loc_after=70,
                               regression=True)
    assert result.verdict == "REFUSE"


def test_churn_only_rejected():
    """Churn-only diffs refuse (no reduction, no value)."""
    assert evaluate_proposal("churn-only", loc_before=100,
                             loc_after=100).verdict == "REFUSE"


def test_test_weakening_rejected():
    """Test-weakening attempts refuse."""
    assert evaluate_proposal("redundant-branch", loc_before=100, loc_after=70,
                             weakens_tests=True).verdict == "REFUSE"


def test_public_api_change_rejected():
    """Public API changes refuse (out of pilot scope)."""
    assert evaluate_proposal("redundant-branch", loc_before=100, loc_after=70,
                             changes_api=True).verdict == "REFUSE"


def test_over_budget_rejected():
    """Over-budget output refuses (write scope stays bounded)."""
    assert WRITE_BUDGET_LINES == 200
    assert evaluate_proposal("redundant-branch", loc_before=100, loc_after=70,
                             over_budget=True).verdict == "REFUSE"


def test_noop_rejected_control_compared():
    """No-op proposals refuse; control slices run without the simplifier."""
    result = evaluate_proposal("noop", loc_before=100, loc_after=100,
                               control_loc=100)
    assert result.verdict == "REFUSE"
    assert result.control_loc == 100


def test_real_ruff_executes():
    """Live tool proof: the pinned ruff actually runs (no stub standing in)."""
    out = run_ruff_check("agent_extensions/sync/simplifier_pilot.py")
    assert out["tool"] == "ruff" and out["version"] and out["exit"] in ("0", "1")
