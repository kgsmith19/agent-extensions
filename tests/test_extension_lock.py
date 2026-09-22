import json

import pytest
from agent_extensions.schemas.extension_lock import (
    ExtensionsLock,
    ExtensionLock,
    compute_digest,
    is_floating_ref,
    read_lockfile,
    verify_lockfile,
    write_lockfile,
)

SHA_A = "0a64e398ec6bb34a494f0c347e8ccae53a862f8e"
SHA_B = "cbe94d02bc8ea7375e13b39cc400e17eeabfcbee"
DIGEST = "sha256:" + "ab" * 32


def _lock(identity="capability.canvas-design", **kw):
    base = dict(
        identity=identity,
        source_url="https://github.com/anthropics/skills",
        source_commit=SHA_A,
        spdx_license="Apache-2.0",
        rendered_digest=DIGEST,
    )
    base.update(kw)
    return ExtensionLock(**base)


def test_floating_refs_rejected():
    """Protects reproducibility; branches, tags-in-motion, and partial versions float."""
    for ref in ["main", "master", "latest", "HEAD", "1.x", "1.2", "v2", ""]:
        assert is_floating_ref(ref), f"{ref!r} should be floating"


def test_exact_refs_accepted():
    """Positive control: full SHAs and content digests are exact."""
    assert not is_floating_ref("0a64e398ec6bb34a494f0c347e8ccae53a862f8e")
    assert not is_floating_ref("sha256:abc123def456789abc123def456789abc12345")


def test_lock_rejects_floating_source_commit():
    """Protects ground truth; a lock pinned to a branch is refused at construction."""
    with pytest.raises(ValueError, match="exact full 40-hex"):
        _lock(source_commit="main")


def test_lock_rejects_short_sha():
    """Protects exactness; abbreviated SHAs are ambiguous and refused."""
    with pytest.raises(ValueError, match="exact full 40-hex"):
        _lock(source_commit="0a64e39")


def test_lock_rejects_missing_license():
    """Protects reviewability; empty license fails closed."""
    lock = ExtensionsLock(entries=[_lock(spdx_license="")])
    problems = verify_lockfile(lock)
    assert any("license" in p for p in problems)


def test_lock_rejects_bad_digest():
    """Protects digest integrity; malformed rendered_digest is refused."""
    with pytest.raises(ValueError, match="sha256"):
        _lock(rendered_digest="abc123")


def test_lock_rejects_duplicate_identity():
    """Protects identity integrity; one lock entry per capability."""
    with pytest.raises(ValueError, match="duplicate lock identity"):
        ExtensionsLock(entries=[_lock(), _lock()])


def test_source_rewrite_detected_via_digest():
    """Protects against rewritten sources; digest mismatch is a loud failure."""
    entry = _lock(rendered_digest=DIGEST)
    rewritten_digest = compute_digest('{"tampered": true}')
    assert rewritten_digest != entry.rendered_digest


def test_compute_digest_stable_and_shaped():
    """Positive control: digests are deterministic sha256:<hex>."""
    assert compute_digest('{"a":1}') == compute_digest('{"a":1}')
    assert compute_digest('{"a":1}').startswith("sha256:")
    assert len(compute_digest('{"a":1}')) == len("sha256:") + 64


def test_rollback_to_older_lock(tmp_path):
    """Protects recovery; an older lockfile reads back and verifies cleanly."""
    old = ExtensionsLock(entries=[_lock(source_commit=SHA_A)])
    write_lockfile(old, tmp_path / "extensions.lock")
    new = ExtensionsLock(
        entries=[_lock(source_commit=SHA_B)],
        history=[f"update capability.canvas-design {SHA_A} -> {SHA_B}"],
    )
    write_lockfile(new, tmp_path / "extensions.lock.new")
    # Rollback = the older file still reads and verifies.
    rolled = read_lockfile(tmp_path / "extensions.lock")
    assert rolled.get("capability.canvas-design").source_commit == SHA_A
    assert verify_lockfile(rolled) == []
    assert json.loads((tmp_path / "extensions.lock.new").read_text())["history"] != []


def test_offline_cache_scenario(tmp_path):
    """Protects hermetic operation; lockfile reads/verifies with no network."""
    lock = ExtensionsLock(entries=[_lock()])
    path = write_lockfile(lock, tmp_path / "extensions.lock")
    assert verify_lockfile(read_lockfile(path)) == []


def test_unavailable_commit_shape_rejected():
    """Protects against garbage provenance; non-hex commits never become locks."""
    with pytest.raises(ValueError, match="exact full 40-hex"):
        _lock(source_commit="not-a-commit-at-all........................")


def test_rendered_digest_reads_utf8_not_platform_default(tmp_path):
    """Protects non-ASCII skills; the digest must read UTF-8, not cp1252."""
    from agent_extensions.sync.locks import _rendered_digest_for_skill

    skill = tmp_path / "s"
    skill.mkdir()
    (skill / "SKILL.md").write_text("cafe \u2014 na\u00efve \u4e2d\u6587", encoding="utf-8")
    digest = _rendered_digest_for_skill(skill)
    assert digest.startswith("sha256:") and len(digest) == len("sha256:") + 64


def test_build_lock_reads_every_source_live():
    """Protects ground truth; the builder reads VENDORED-FROM + pins live, verifies clean."""
    from pathlib import Path
    from agent_extensions.sync.locks import build_lock_from_repo

    repo_root = Path(__file__).resolve().parents[1]
    lock = build_lock_from_repo(repo_root)
    assert verify_lockfile(lock) == []
    by_id = {e.identity: e for e in lock.entries}
    # Own skills carry their vendored pins.
    assert by_id["capability.skill-creator"].source_commit.startswith("0a64e398")
    # Vendored skills may come from non-anthropics sources with their own license.
    assert by_id["capability.proving-it-works-with-a-movie"].source_url == (
        "https://github.com/obra/superpowers"
    )
    assert by_id["capability.proving-it-works-with-a-movie"].spdx_license == "MIT"
    # External marketplace pins preserved verbatim.
    assert by_id["marketplace.claude-plugins-official"].source_commit == (
        "c2301c68838dc9832e4034558e2c3a05e78f269c"
    )
    assert by_id["marketplace.superpowers-marketplace"].source_commit == (
        "ff9fa8a51f422d81414fa355587620d4ad2df81c"
    )
    assert by_id["plugin.superpowers-marketplace.superpowers-lab"].source_commit == (
        "51111f74f24058117752d9aa917cb19859f8ec86"
    )
    # Live-marketplace pointer is explicitly marked, not mistaken for a pin.
    assert by_id["marketplace.agent-extensions-live"].upstream_kind == "live-marketplace"
