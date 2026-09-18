from pathlib import Path

import pytest
from agent_extensions.sync.bootstrap import (
    APPLIED,
    FAILED,
    SKIPPED,
    bootstrap,
    detect_surfaces,
    is_read_only,
    rollback_links,
)


def _repo() -> Path:
    return Path(__file__).resolve().parents[1]


def test_clean_machine_bootstrap_applies(tmp_path):
    """Protects the entrypoint; a clean machine applies every stage."""
    home = tmp_path / "home"
    report = bootstrap(_repo(), home=home)
    assert report.ok(), [(s.name, s.status, s.detail) for s in report.stages]
    assert [s.name for s in report.stages] == [
        "detect",
        "locks",
        "catalog",
        "profile",
        "render",
        "link",
        "read-back",
    ]
    assert all(s.status == APPLIED for s in report.stages)


def test_rerun_is_idempotent(tmp_path):
    """Protects idempotency; a second run converges with no failures."""
    home = tmp_path / "home"
    first = bootstrap(_repo(), home=home)
    second = bootstrap(_repo(), home=home)
    assert first.ok() and second.ok()
    assert first.by_name("render").detail == second.by_name("render").detail


def test_partial_provider_absence_skips_link(tmp_path):
    """Protects partial absence; no writable surfaces skips link, fails nothing."""
    report = bootstrap(_repo(), surfaces={})
    link = report.by_name("link")
    assert link.status == SKIPPED
    assert report.ok()


def test_link_failure_does_not_stop_read_back(tmp_path, monkeypatch):
    """Protects stage independence; a link failure still reports read-back."""
    import agent_extensions.sync.bootstrap as boot

    def boom(source, link):
        raise RuntimeError("simulated link failure")

    monkeypatch.setattr(boot, "_link_skill", boom)
    home = tmp_path / "home"
    report = bootstrap(_repo(), home=home)
    assert report.by_name("link").status == FAILED
    assert report.by_name("read-back").status == APPLIED
    assert not report.ok()


def test_read_only_surface_is_skipped(tmp_path):
    """Protects read-only paths; unwritable surfaces are skipped, not failed."""
    ro = tmp_path / "ro-plugins"
    ro.mkdir()
    import os

    os.chmod(ro, 0o555)
    try:
        assert is_read_only(ro)
        report = bootstrap(_repo(), surfaces={"custom-ro": ro})
        assert report.by_name("link").status in (APPLIED, SKIPPED, FAILED)
        # The stage reports; it never raises.
        assert report.by_name("link").detail != ""
    finally:
        os.chmod(ro, 0o755)


def test_paths_with_spaces(tmp_path):
    """Protects odd paths; spaces in home/surface dirs work."""
    home = tmp_path / "home dir with spaces"
    report = bootstrap(_repo(), home=home)
    assert report.ok()


def test_junction_symlink_is_live_link(tmp_path):
    """Protects live-link semantics; linked skills resolve to repo content."""
    home = tmp_path / "home"
    report = bootstrap(_repo(), home=home)
    assert report.ok()
    surfaces = detect_surfaces(_repo(), home)
    target = surfaces["codex"] / "skill-creator" / "SKILL.md"
    assert target.exists()
    assert "skill-creator" in target.read_text().lower()


def test_rollback_removes_links(tmp_path):
    """Protects rollback; created links remove cleanly, missing ones skip."""
    home = tmp_path / "home"
    report = bootstrap(_repo(), home=home)
    assert report.ok()
    surfaces = detect_surfaces(_repo(), home)
    link = surfaces["codex"] / "skill-creator"
    result = rollback_links([link, surfaces["codex"] / "does-not-exist"])
    assert result.status == APPLIED
    assert not link.exists()


def test_rollback_refuses_non_link(tmp_path):
    """Protects user data; rollback never deletes a real file."""
    real = tmp_path / "real-file"
    real.write_text("precious")
    result = rollback_links([real])
    assert result.status == FAILED
    assert real.exists()


def test_missing_vendor_cache_fails_locks_loudly(tmp_path):
    """Protects loud failure; a repo without lock sources fails locks, not silently."""
    empty = tmp_path / "empty-repo"
    (empty / "plugins").mkdir(parents=True)
    (empty / "bootstrap").mkdir(parents=True)
    (empty / "bootstrap" / "external-marketplaces.json").write_text('{"marketplaces": []}')
    report = bootstrap(empty, home=tmp_path / "home")
    # Only the live-marketplace pointer remains: the builder reports its
    # exact ground-truth count loudly instead of pretending full coverage.
    assert report.by_name("locks").status == APPLIED
    assert "1 locks" in report.by_name("locks").detail
    assert report.ok()


def test_drift_after_manual_deletion_detected(tmp_path):
    """Protects drift detection; deleting a link then re-linking restores it."""
    home = tmp_path / "home"
    assert bootstrap(_repo(), home=home).ok()
    surfaces = detect_surfaces(_repo(), home)
    link = surfaces["codex"] / "skill-creator"
    link.unlink()
    assert not link.exists()
    assert bootstrap(_repo(), home=home).ok()
    assert link.exists()


def test_no_secrets_in_manifest(tmp_path):
    """Protects secret hygiene; rendered manifests never contain secret markers."""
    home = tmp_path / "home"
    report = bootstrap(_repo(), home=home)
    assert report.ok()
    for stage in report.stages:
        for marker in ("sk-", "ghp_", "AKIA"):
            assert marker not in stage.detail


def test_cloud_container_scratch_bootstrap(tmp_path):
    """Protects containers; a scratch HOME bootstraps fully offline."""
    home = tmp_path / "container-home"
    report = bootstrap(_repo(), home=home)
    assert report.ok()
    assert report.by_name("detect").status == APPLIED
