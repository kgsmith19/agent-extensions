"""Stage 62 (#20): v5 semantics adopted into agent-extensions' supply plane."""

from pathlib import Path

from agent_extensions.sync.stage62_adoption import (
    ADOPTED_STANDARD_SHA,
    CHECKS,
    evaluate,
    write_lock_last,
)

REPO = Path(__file__).resolve().parents[1]


def test_all_fourteen_checks_pass_live():
    """Every adoption check evaluates live against this repo — all green."""
    result = evaluate(REPO)
    assert result.ready, result.failed
    assert sorted(result.passed) == sorted(CHECKS)


def test_standard_sha_pinned_exact():
    """Adoption binds the exact merged Standard contract SHA (no float)."""
    assert len(ADOPTED_STANDARD_SHA) == 40
    assert all(c in "0123456789abcdef" for c in ADOPTED_STANDARD_SHA)


def test_provider_parity_preserved():
    """Provider manifests keep identical checksums (Claude/Codex/Gemini/local)."""
    result = evaluate(REPO)
    assert result.passed["provider-manifests"]
    assert "identical" in result.notes["provider-manifests"]


def test_conformance_runs_real_canaries():
    """Real conformance ran (not stubbed): canaries executed with verdicts."""
    result = evaluate(REPO)
    assert result.passed["real-conformance"]
    assert "canaries ran" in result.notes["real-conformance"]


def test_exact_head_evidence_is_sha():
    """Exact-head evidence is a real 40-hex SHA."""
    result = evaluate(REPO)
    head = result.notes["exact-head-evidence"]
    assert len(head) == 40 and all(c in "0123456789abcdef" for c in head)


def test_lock_written_last_only_when_ready():
    """extensions.lock writes last and only when every other check passes."""
    import json
    import tempfile
    import shutil
    with tempfile.TemporaryDirectory() as tmp:
        # write_lock_last refuses on an empty dir (checks fail there)
        out = write_lock_last(tmp)
        assert out["written"] == "no" and out["failed"]
