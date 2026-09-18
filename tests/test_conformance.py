from pathlib import Path

from agent_extensions.sync.conformance import (
    Status,
    Tier,
    run_conformance,
    write_canary_artifacts,
)

REPO = Path(__file__).resolve().parents[1]


def test_conformance_reproduces_missing_plugin_json():
    """Protects the regression; the refusal path fires (DEGRADED verdict = gate works)."""
    report = run_conformance(REPO, providers=["codex"])
    found = [r for r in report.results if r.name == "missing-plugin-json"]
    assert found and all(r.status == Status.DEGRADED for r in found)
    assert all("plugin.json" in r.log for r in found)
    assert all("refuse to install" in r.detail for r in found)


def test_conformance_catches_url_drift_per_provider():
    """Protects transport fidelity across every provider under test."""
    report = run_conformance(REPO, providers=["codex", "claude", "antigravity"])
    drift = [r for r in report.results if r.name == "url-serverurl-drift"]
    assert len(drift) == 3
    assert all(r.status == Status.VERIFIED for r in drift)


def test_conformance_hook_env_not_noop():
    """Protects hook bridging; the wrapper provably bridges, not no-ops."""
    report = run_conformance(REPO, providers=["codex"])
    found = [r for r in report.results if r.name == "hook-env-noop"]
    assert found and found[0].status == Status.VERIFIED
    assert "hook-bridging" in found[0].capabilities_proven


def test_conformance_auth_from_logs_not_exit_code():
    """Protects diagnosis discipline in the canary itself."""
    report = run_conformance(REPO, providers=["codex"])
    found = [r for r in report.results if r.name == "auth-detail-in-logs"]
    assert found and found[0].status == Status.VERIFIED


def test_conformance_partial_sync_reports():
    """Protects partial-sync honesty; the real verify script runs inside."""
    report = run_conformance(REPO, providers=["codex"])
    found = [r for r in report.results if r.name == "partial-sync"]
    assert found and found[0].command.startswith("pwsh bootstrap/verify-content.ps1")
    assert found[0].log != ""


def test_conformance_cross_platform_paths():
    """Protects path portability as a live canary, not just a unit test."""
    report = run_conformance(REPO, providers=["codex"])
    found = [r for r in report.results if r.name == "cross-platform-paths"]
    assert found and found[0].status == Status.VERIFIED


def test_conformance_silent_zero_skills_caught():
    """Protects the silent-success regression; empty roster would DEGRADED."""
    report = run_conformance(REPO, providers=["codex"])
    found = [r for r in report.results if r.name == "silent-zero-skills"]
    assert found and found[0].status == Status.VERIFIED
    assert len(found[0].capabilities_proven) > 0


def test_failing_canary_lowers_autonomy():
    """Protects auto-degrade; any DEGRADED forces supervised-only + safe alternative."""
    report = run_conformance(REPO, providers=["codex"])
    assert report.degraded(), "expected honest DEGRADED entries (auth + web/mobile)"
    assert report.autonomy().startswith("supervised-only")
    for result in report.degraded():
        assert result.detail != "", f"{result.name} degrades without a safe alternative"


def test_web_mobile_gap_honest_not_worked_around():
    """Protects honesty; web/mobile read-back is a declared gap."""
    report = run_conformance(REPO, providers=["codex"])
    found = [r for r in report.results if r.name == "web-mobile-readback"]
    assert found and found[0].status == Status.DEGRADED
    assert "Stage 24" in found[0].log


def test_unknown_provider_degrades_with_fallback_pointer():
    """Protects extension; unknown providers point at the generic fallback."""
    report = run_conformance(REPO, providers=["clippy"])
    found = [r for r in report.results if r.name == "unknown-provider"]
    assert found and found[0].status == Status.DEGRADED
    assert "fallback" in found[0].detail


def test_artifacts_record_everything(tmp_path):
    """Protects auditability; artifacts carry versions, commands, logs, status."""
    report = run_conformance(REPO, providers=["codex"])
    out = write_canary_artifacts(report, tmp_path / "canaries")
    import json

    payload = json.loads(out.read_text())
    assert payload["autonomy"].startswith("supervised-only")
    for entry in payload["results"]:
        for key in ("provider", "harness_version", "command", "log",
                    "capabilities_proven", "status"):
            assert key in entry, key
        assert entry["status"] in ("VERIFIED", "PROMOTED", "DEGRADED", "PROPOSED")


def test_no_false_promoted():
    """Protects status integrity; PROMOTED appears only on a fully-clean run."""
    report = run_conformance(REPO, providers=["codex"])
    assert not [r for r in report.results if r.status == Status.PROMOTED]
    assert all(r.tier != Tier.AUTHENTICATED or r.status == Status.DEGRADED
               for r in report.results)
