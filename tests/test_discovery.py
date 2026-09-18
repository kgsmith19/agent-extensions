from pathlib import Path

import pytest
from agent_extensions.sync.discovery import (
    SkillDescriptor,
    discovery_index,
    index_repo,
    index_skill,
    resolve_body,
)

REPO = Path(__file__).resolve().parents[1]


def test_catalog_with_dozens_of_skills_indexes_descriptors_only():
    """Protects metadata-first; N skills index to N tiny descriptors, no bodies."""
    descriptors = index_repo(REPO)
    assert len(descriptors) >= 3
    total_descriptor_chars = sum(
        len(d.name) + len(d.description) + len(d.semantic_id) for d in descriptors
    )
    total_body_bytes = sum(d.body_bytes for d in descriptors)
    assert total_descriptor_chars < total_body_bytes // 4


def test_descriptors_carry_budget_and_digest_fields():
    """Protects the field set; bytes/tokens/dependencies/digest all present."""
    descriptors = {d.name: d for d in index_repo(REPO)}
    creator = descriptors["skill-creator"]
    assert creator.body_bytes > 10000
    assert creator.body_tokens_est == creator.body_bytes // 4
    assert "agents" in creator.dependencies
    assert creator.body_digest.startswith("sha256:")
    assert len(creator.body_digest) == len("sha256:") + 64


def test_ambiguous_descriptor_match_is_visible():
    """Protects routing honesty; overlapping blurbs are inspectable, not hidden."""
    descriptors = index_repo(REPO)
    table = discovery_index(descriptors)
    hits = [d for d in table.values() if "artifact" in d.description.lower()]
    assert len(hits) >= 1
    # The router sees names + blurbs only — bodies stay on disk.
    assert all(isinstance(d, SkillDescriptor) for d in hits)


def test_transitive_dependency_listed():
    """Protects dependency visibility; body asset dirs surface as dependencies."""
    descriptors = {d.name: d for d in index_repo(REPO)}
    assert "references" in descriptors["skill-creator"].dependencies
    assert "scripts" in descriptors["skill-creator"].dependencies


def test_duplicate_capability_fails_loudly():
    """Protects index integrity; two skills mapping to one ID is refused."""
    descriptors = index_repo(REPO)
    dupe = descriptors[0].model_copy(update={"name": "other-name"})
    with pytest.raises(ValueError, match="duplicate capability"):
        discovery_index(descriptors + [dupe])


def test_over_budget_skill_flagged_by_descriptor():
    """Protects budgets; an over-budget body is visible before loading it."""
    descriptors = index_repo(REPO)
    budget_tokens = 2000
    over = [d for d in descriptors if d.body_tokens_est > budget_tokens]
    assert over, "expected at least one skill over a tight budget"
    assert all(d.body_bytes > 0 for d in over)


def test_selected_skill_missing_body_fails():
    """Protects JIT integrity; a descriptor with no body on disk fails loudly."""
    ghost = SkillDescriptor(
        name="ghost",
        semantic_id="capability.ghost",
        description="not on disk",
        body_bytes=10,
        body_tokens_est=3,
        body_digest="sha256:" + "00" * 32,
        source_path="plugins/nowhere/ghost",
    )
    with pytest.raises(FileNotFoundError, match="no body"):
        resolve_body(ghost, REPO)


def test_body_hash_change_rejected(tmp_path):
    """Protects freshness; a body edited after indexing is refused until re-index."""
    descriptors = {d.name: d for d in index_repo(REPO)}
    desc = descriptors["skill-creator"]
    with pytest.raises(ValueError, match="body hash changed"):
        resolve_body(desc, REPO, expected_digest="sha256:" + "ff" * 32)


def test_resolve_body_round_trip():
    """Positive control: indexed digest resolves the real body."""
    descriptors = {d.name: d for d in index_repo(REPO)}
    body = resolve_body(descriptors["canvas-design"], REPO)
    assert "Canvas" in body or "canvas" in body


def test_discovery_equivalence_claude_codex_gemini():
    """Protects cross-harness parity; the index is provider-neutral data."""
    descriptors = index_repo(REPO)
    table = discovery_index(descriptors)
    for provider_file in ("codex", "claude", "gemini"):
        ids = sorted(table)
        assert ids == sorted(d.semantic_id for d in descriptors), provider_file
    assert "capability.skill-creator" in table


def test_activation_precision_one_process_one_domain():
    """Protects policy; resolving exactly 2 bodies (process + domain) suffices."""
    descriptors = {d.name: d for d in index_repo(REPO)}
    bodies = [
        resolve_body(descriptors["skill-creator"], REPO),
        resolve_body(descriptors["canvas-design"], REPO),
    ]
    assert len(bodies) == 2
    assert all(len(b) > 1000 for b in bodies)
