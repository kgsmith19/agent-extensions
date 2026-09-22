"""T17 (INT-02) evidence tiers: AC1-AC6 with must-never-happen guards."""

from agent_extensions.sync.evidence_tiers import (
    EvidenceTier,
    Health,
    aggregate_health,
    auth_unrun_health,
    make_verdict,
    negative_stays_green,
    parse_manifest_quarantined,
    validate_verdict,
    QUARANTINE_SCOPE,
)

ADAPTER_V = "adapters/1.0.0"
HARNESS_V = "agent-extensions-pytest-harness/1.0"


def _v(cap="capability.x", tier=EvidenceTier.RUNTIME, health=Health.HEALTHY,
       executed=True, **kw):
    base = dict(adapter_version=ADAPTER_V, harness_version=HARNESS_V)
    base.update(kw)
    return make_verdict(cap, tier, health, executed=executed, **base)


def test_negatives_green_targets_clean():
    """AC1: passing negatives stay green; live-target aggregate stays healthy."""
    assert negative_stays_green(True) == Health.HEALTHY
    verdicts = [_v(health=Health.HEALTHY)]
    assert aggregate_health(verdicts)["health"] == Health.HEALTHY.value


def test_substring_is_not_runtime():
    """AC2/AC6: a name match without execution never validates as runtime."""
    verdict = _v(tier=EvidenceTier.RUNTIME, executed=False)
    problems = validate_verdict(verdict)
    assert any("not runtime" in p for p in problems)
    structural = _v(tier=EvidenceTier.STRUCTURAL, executed=False)
    assert validate_verdict(structural) == []


def test_auth_unrun_degraded_never_failed_or_healthy():
    """AC3: credential-absent capabilities report degraded, nothing else."""
    health = auth_unrun_health()
    assert health == Health.DEGRADED
    assert health != Health.FAILED and health != Health.HEALTHY
    verdicts = [_v(health=auth_unrun_health())]
    assert aggregate_health(verdicts)["health"] == Health.DEGRADED.value


def test_versions_attached_every_verdict():
    """AC4: versionless verdicts are rejected."""
    verdict = make_verdict("capability.x", EvidenceTier.STRUCTURAL,
                           Health.HEALTHY, executed=False,
                           adapter_version="", harness_version="")
    problems = validate_verdict(verdict)
    assert any("versionless" in p for p in problems)
    assert validate_verdict(_v()) == []


def test_broken_manifest_isolated_from_health():
    """AC5: malformed manifests quarantine to their own scope; live health unchanged."""
    live = [_v(health=Health.HEALTHY)]
    before = aggregate_health(live)["health"]
    ok, scope, payload = parse_manifest_quarantined("{not json")
    assert not ok and scope == QUARANTINE_SCOPE and payload is None
    after = aggregate_health(live)["health"]
    assert before == after == Health.HEALTHY.value


def test_tier_discipline_no_collapse():
    """AC6: structural/translation evidence never validates as runtime."""
    for tier in (EvidenceTier.STRUCTURAL, EvidenceTier.TRANSLATION):
        verdict = _v(tier=tier, executed=False)
        assert validate_verdict(verdict) == []
        assert verdict.tier != EvidenceTier.RUNTIME
    runtime_unexecuted = _v(tier=EvidenceTier.RUNTIME, executed=False)
    assert validate_verdict(runtime_unexecuted) != []


def test_must_never_happen_guards():
    """Must-never-happen: false unhealthy, fake runtime, failed/healthy unrun."""
    # Passing negative must not mark targets unhealthy.
    assert aggregate_health([_v(health=Health.HEALTHY)])["health"] != Health.FAILED.value
    # Auth-unrun must be degraded exactly.
    assert auth_unrun_health().value == "degraded"
    # Versionless must not validate.
    bad = make_verdict("c", EvidenceTier.RUNTIME, Health.HEALTHY,
                       executed=True, adapter_version="",
                       harness_version=HARNESS_V)
    assert validate_verdict(bad) != []
