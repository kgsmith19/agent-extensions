from pathlib import Path

import pytest
from agent_extensions.sync.surfaces import (
    AUTOMATED,
    GENERATED_MANUAL,
    UNSUPPORTED,
    ManualBundle,
    declare_surfaces,
    diff_manual_state,
    generate_manual_bundle,
    is_stale_last_applied,
    read_account_manifest,
    read_last_applied,
    record_last_applied,
)

REPO = Path(__file__).resolve().parents[1]


def test_fresh_cloud_container_bootstraps(tmp_path):
    """Protects containers; scratch HOME bootstraps fully offline (Stage 18 entrypoint)."""
    from agent_extensions.sync.bootstrap import bootstrap

    report = bootstrap(REPO, home=tmp_path / "container-home")
    assert report.ok()
    assert report.by_name("detect").status == "applied"


def test_absent_provider_is_declared_gap():
    """Protects honesty; surfaces with no API are UNSUPPORTED, never fake-automated."""
    table = {d.surface: d for d in declare_surfaces()}
    assert table["web"].mode == UNSUPPORTED
    assert table["mobile-api"].mode == UNSUPPORTED
    assert "no public API" in table["web"].reason


def test_three_skill_account_manifest():
    """Protects the roster; exactly the 3 known skills, empty connectors."""
    manifest = read_account_manifest(REPO)
    assert sorted(s["name"] for s in manifest["skills"]) == [
        "canvas-design", "skill-creator", "web-artifacts-builder",
    ]
    assert manifest["connectors"] == []


def test_empty_connector_list_checklist(tmp_path):
    """Protects connector honesty; empty list yields an explicit nothing-to-do item."""
    bundle = generate_manual_bundle(REPO, "claude-web", tmp_path / "bundle")
    assert "connectors: none declared — nothing to connect" in bundle.checklist


def test_manual_add_remove_diff():
    """Protects manual ops; add/remove computed, person executes."""
    current = {"skills": [{"name": "a"}, {"name": "b"}], "connectors": []}
    last = {"skills": [{"name": "a"}, {"name": "old"}], "connectors": []}
    diff = diff_manual_state(current, last)
    assert diff["to_add"] == ["b"]
    assert diff["to_remove"] == ["old"]


def test_stale_last_applied_detected():
    """Protects freshness; roster drift since last apply is stale, not current."""
    assert is_stale_last_applied(
        {"skills": [{"name": "a"}, {"name": "b"}]},
        {"skills": [{"name": "a"}]},
    )
    assert not is_stale_last_applied(
        {"skills": [{"name": "a"}]}, {"skills": [{"name": "a"}]}
    )


def test_no_public_api_case_declared(tmp_path):
    """Protects the non-goal; bundles refuse unsupported surfaces loudly."""
    with pytest.raises(ValueError, match="manual bundles exist only"):
        generate_manual_bundle(REPO, "web", tmp_path / "bundle")


def test_secret_scan_before_distribution(tmp_path, monkeypatch):
    """Protects distribution; a secreted skill aborts the bundle, Loudly."""
    import agent_extensions.sync.surfaces as surfaces

    real_read = Path.read_text

    def tainted(self, *a, **k):
        text = real_read(self, *a, **k)
        if self.name == "SKILL.md":
            return text + "\nkey=sk-ant-abcdefgh12345678"
        return text

    monkeypatch.setattr(Path, "read_text", tainted)
    with pytest.raises(RuntimeError, match="secret scan failed"):
        generate_manual_bundle(REPO, "claude-web", tmp_path / "bundle")


def test_generated_bundle_digest(tmp_path):
    """Protects bundle integrity; digest covers exactly the staged files."""
    bundle = generate_manual_bundle(REPO, "claude-web", tmp_path / "bundle")
    assert bundle.digest.startswith("sha256:")
    assert len(bundle.staged_files) == 3
    assert (tmp_path / "bundle" / "CHECKLIST.md").exists()
    assert not (tmp_path / "bundle" / "skill-creator" / "SKILL.md").is_symlink()


def test_automated_vs_manual_table():
    """Protects the deliverable; every surface has exactly one honest mode."""
    table = declare_surfaces()
    by_mode = {}
    for decl in table:
        by_mode.setdefault(decl.mode, []).append(decl.surface)
        assert decl.reason != ""
    assert set(by_mode) == {AUTOMATED, GENERATED_MANUAL, UNSUPPORTED}
    assert "claude-code" in by_mode[AUTOMATED]
    assert "claude-web" in by_mode[GENERATED_MANUAL]


def test_record_last_applied(tmp_path):
    """Protects state tracking; recording persists the current roster (test-local)."""
    import json
    import shutil

    scratch = tmp_path / "repo"
    shutil.copytree(REPO / "bootstrap", scratch / "bootstrap")
    shutil.copytree(REPO / "plugins", scratch / "plugins")
    bundle = generate_manual_bundle(scratch, "claude-mobile", tmp_path / "bundle")
    assert isinstance(bundle, ManualBundle)
    path = record_last_applied(scratch, bundle)
    assert json.loads(path.read_text())["skills"] != []
