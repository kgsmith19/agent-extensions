"""Model tiers rendered into harnesses (issue #81).

Contract (std#303): roles orchestrator->planning, implementer->fast,
reviewer->planning; built-in profile anthropic {planning: opus, fast: haiku};
owner overrides from ~/.config/agents/agent-tiers.json, then AE_TIER_* env.
"""
import json
from pathlib import Path

import pytest

from agent_extensions.install import MANAGED_MD_MARKER
from agent_extensions.install.machine import bootstrap, stage_tiers
from agent_extensions.install.tiers import (
    DEFAULT_TIERS,
    TIER_SUPPORT,
    TierConfigError,
    resolve_tiers,
)


@pytest.fixture()
def home(tmp_path, monkeypatch):
    h = tmp_path / "home"
    h.mkdir()
    monkeypatch.setenv("HOME", str(h))
    monkeypatch.setenv("USERPROFILE", str(h))
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(h / ".gitconfig"))
    for key in ("AE_TIER_PLANNING", "AE_TIER_FAST"):
        monkeypatch.delenv(key, raising=False)
    return h


def _repo() -> Path:
    return Path(__file__).resolve().parents[1]


def _claude(home: Path) -> Path:
    p = home / ".claude"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _tier_file(home: Path) -> Path:
    return home / ".config" / "agents" / "agent-tiers.json"


def _write_tiers(home: Path, data) -> None:
    p = _tier_file(home)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(data if isinstance(data, str) else json.dumps(data), encoding="utf-8")


def _impl(home: Path) -> Path:
    return home / ".claude" / "agents" / "implementer.md"


def _rev(home: Path) -> Path:
    return home / ".claude" / "agents" / "reviewer.md"


# ---- AC1: resolution and validation ----


def test_defaults_when_no_file_and_no_env(home):
    t = resolve_tiers(home, {})
    assert t["roles"] == {"orchestrator": "planning", "implementer": "fast", "reviewer": "planning"}
    assert t["profiles"] == {"anthropic": {"planning": "opus", "fast": "haiku"}}
    assert t["fallback"] == "harness-default"
    assert t == DEFAULT_TIERS


def test_file_overrides_defaults(home):
    _write_tiers(home, {"profiles": {"anthropic": {"planning": "opus", "fast": "sonnet"}}})
    t = resolve_tiers(home, {})
    assert t["profiles"]["anthropic"]["fast"] == "sonnet"
    assert t["roles"]["implementer"] == "fast"


def test_env_overrides_file_which_overrides_defaults(home):
    _write_tiers(home, {"profiles": {"anthropic": {"planning": "sonnet", "fast": "sonnet"}}})
    t = resolve_tiers(home, {"AE_TIER_FAST": "haiku"})
    assert t["profiles"]["anthropic"]["fast"] == "haiku"  # env wins over file
    assert t["profiles"]["anthropic"]["planning"] == "sonnet"  # file wins over default


def test_env_planning_overrides_default_without_file(home):
    t = resolve_tiers(home, {"AE_TIER_PLANNING": "sonnet"})
    assert t["profiles"]["anthropic"]["planning"] == "sonnet"
    assert t["profiles"]["anthropic"]["fast"] == "haiku"


def test_empty_env_value_is_ignored(home):
    t = resolve_tiers(home, {"AE_TIER_FAST": "", "AE_TIER_PLANNING": "   "})
    assert t == DEFAULT_TIERS


def test_invalid_json_names_the_file(home):
    _write_tiers(home, "{not json")
    with pytest.raises(TierConfigError) as exc:
        resolve_tiers(home, {})
    assert "agent-tiers.json" in str(exc.value)


def test_unknown_top_level_key_rejected(home):
    _write_tiers(home, {"roles": {}, "surprise": 1})
    with pytest.raises(TierConfigError, match="surprise"):
        resolve_tiers(home, {})


def test_role_pointing_at_missing_tier_rejected(home):
    _write_tiers(home, {"roles": {"implementer": "speedy"}})
    with pytest.raises(TierConfigError, match="speedy"):
        resolve_tiers(home, {})


def test_profile_missing_a_role_tier_rejected(home):
    _write_tiers(home, {"profiles": {"anthropic": {"planning": "opus"}}})
    with pytest.raises(TierConfigError, match="fast"):
        resolve_tiers(home, {})


def test_fallback_must_be_a_string(home):
    _write_tiers(home, {"fallback": 3})
    with pytest.raises(TierConfigError, match="fallback"):
        resolve_tiers(home, {})


def test_model_alias_must_be_a_safe_token(home):
    """A model value lands in frontmatter; newlines or spaces must never get there."""
    _write_tiers(home, {"profiles": {"anthropic": {"planning": "opus", "fast": "haiku\nname: evil"}}})
    with pytest.raises(TierConfigError):
        resolve_tiers(home, {})


def test_top_level_must_be_an_object(home):
    _write_tiers(home, ["opus"])
    with pytest.raises(TierConfigError):
        resolve_tiers(home, {})


# ---- AC2: the tiers stage ----


def test_renders_exact_model_lines_from_defaults(home):
    _claude(home)
    r = stage_tiers(_repo(), home, env={})
    assert r.status == "applied", r.detail
    assert "\nmodel: haiku\n" in _impl(home).read_text(encoding="utf-8")
    assert "\nmodel: opus\n" in _rev(home).read_text(encoding="utf-8")


def test_rendered_frontmatter_first_then_marker(home):
    _claude(home)
    stage_tiers(_repo(), home, env={})
    for path, name in ((_impl(home), "implementer"), (_rev(home), "reviewer")):
        text = path.read_text(encoding="utf-8")
        assert text.startswith("---\n"), path
        head, _, rest = text[4:].partition("\n---\n")
        assert f"name: {name}" in head.splitlines()
        assert any(line.startswith("description: ") for line in head.splitlines())
        assert any(line.startswith("model: ") for line in head.splitlines())
        assert text.count(MANAGED_MD_MARKER) == 1
        assert MANAGED_MD_MARKER in rest
        assert "{model}" not in text


def test_reviewer_declares_read_only_tools(home):
    _claude(home)
    stage_tiers(_repo(), home, env={})
    text = _rev(home).read_text(encoding="utf-8")
    assert "tools: Read, Grep, Glob, Bash" in text
    assert "never edit" in text.lower()


def test_implementer_body_carries_limits_and_skills(home):
    _claude(home)
    stage_tiers(_repo(), home, env={})
    text = _impl(home).read_text(encoding="utf-8")
    for needle in (
        "own isolated git worktree",
        "never push",
        "queue write",
        "stop and report",
        "test-driven-development",
        "systematic-debugging",
        "verification-before-completion",
        "using-git-worktrees",
        "oracle",
    ):
        assert needle in text, needle


def test_env_override_changes_rendered_model(home):
    _claude(home)
    stage_tiers(_repo(), home, env={"AE_TIER_FAST": "sonnet"})
    assert "\nmodel: sonnet\n" in _impl(home).read_text(encoding="utf-8")
    assert "\nmodel: opus\n" in _rev(home).read_text(encoding="utf-8")


def test_file_override_changes_reviewer_model(home):
    _claude(home)
    _write_tiers(home, {"profiles": {"anthropic": {"planning": "sonnet", "fast": "haiku"}}})
    stage_tiers(_repo(), home, env={})
    assert "\nmodel: sonnet\n" in _rev(home).read_text(encoding="utf-8")


def test_invalid_file_fails_and_writes_nothing(home):
    _claude(home)
    _write_tiers(home, "{broken")
    r = stage_tiers(_repo(), home, env={})
    assert r.status == "failed"
    assert "agent-tiers.json" in r.detail
    assert not _impl(home).exists()
    assert not _rev(home).exists()


def test_invalid_shape_fails_and_writes_nothing(home):
    _claude(home)
    _write_tiers(home, {"roles": {"implementer": "nope"}})
    r = stage_tiers(_repo(), home, env={})
    assert r.status == "failed"
    assert not (home / ".claude" / "agents").exists()


def test_idempotent_rerun_does_not_rewrite(home):
    _claude(home)
    stage_tiers(_repo(), home, env={})
    t_impl = _impl(home).stat().st_mtime_ns
    t_rev = _rev(home).stat().st_mtime_ns
    r = stage_tiers(_repo(), home, env={})
    assert r.status == "applied"
    assert r.detail.count("up to date") == 2, r.detail
    assert _impl(home).stat().st_mtime_ns == t_impl
    assert _rev(home).stat().st_mtime_ns == t_rev


def test_managed_file_converges_on_model_change(home):
    _claude(home)
    stage_tiers(_repo(), home, env={})
    stage_tiers(_repo(), home, env={"AE_TIER_FAST": "sonnet"})
    assert "\nmodel: sonnet\n" in _impl(home).read_text(encoding="utf-8")


def test_unmanaged_agent_file_never_clobbered(home):
    _claude(home)
    (home / ".claude" / "agents").mkdir()
    _impl(home).write_text("---\nname: implementer\nmodel: inherit\n---\nKEEP ME\n", encoding="utf-8")
    r = stage_tiers(_repo(), home, env={})
    assert "KEEP ME" in _impl(home).read_text(encoding="utf-8")
    assert "implementer: skipped (unmanaged" in r.detail
    assert "\nmodel: opus\n" in _rev(home).read_text(encoding="utf-8")


def test_skipped_without_claude_dir(home):
    r = stage_tiers(_repo(), home, env={})
    assert r.status == "skipped"
    assert ".claude" in r.detail
    assert not (home / ".claude").exists()


def test_gaps_are_listed_by_name_and_never_claimed(home):
    _claude(home)
    r = stage_tiers(_repo(), home, env={})
    for harness in ("codex", "gemini", "kilo", "pi", "local"):
        assert harness in r.detail
        assert TIER_SUPPORT[harness].startswith("gap:")
    assert TIER_SUPPORT["claude-code"].startswith("rendered:")


def test_gaps_listed_even_when_skipped(home):
    r = stage_tiers(_repo(), home, env={})
    for harness in ("codex", "gemini", "kilo", "pi", "local"):
        assert harness in r.detail


def test_bootstrap_runs_tiers_right_after_shims(home):
    report = bootstrap(_repo(), home=home)
    names = [s.name for s in report.stages]
    assert names[:6] == ["bundle", "gitignore", "shims", "tiers", "hooks", "cli-shim"]


# ---- AC4: ae status lines ----


def _status(home: Path, monkeypatch) -> str:
    from agent_extensions.install.__main__ import _probe_status

    monkeypatch.delenv("AE_TIER_FAST", raising=False)
    monkeypatch.delenv("AE_TIER_PLANNING", raising=False)
    return _probe_status(_repo(), home)


def test_status_tiers_applied_when_current(home, monkeypatch):
    _claude(home)
    stage_tiers(_repo(), home, env={})
    out = _status(home, monkeypatch)
    assert "applied  tiers/claude: implementer" in out
    assert "applied  tiers/claude: reviewer" in out


def test_status_tiers_drifted_when_edited(home, monkeypatch):
    _claude(home)
    stage_tiers(_repo(), home, env={})
    _impl(home).write_text(_impl(home).read_text(encoding="utf-8").replace("model: haiku", "model: opus"), encoding="utf-8")
    assert "drifted  tiers/claude: implementer" in _status(home, monkeypatch)


def test_status_tiers_drifted_when_missing(home, monkeypatch):
    _claude(home)
    assert "drifted  tiers/claude: implementer" in _status(home, monkeypatch)


def test_status_tiers_unmanaged(home, monkeypatch):
    _claude(home)
    (home / ".claude" / "agents").mkdir()
    _rev(home).write_text("mine\n", encoding="utf-8")
    assert "unmanaged tiers/claude: reviewer" in _status(home, monkeypatch)


def test_status_tiers_skipped_without_claude(home, monkeypatch):
    out = _status(home, monkeypatch)
    assert "skipped  tiers/claude: gate absent" in out


def test_status_tiers_drifted_on_invalid_config(home, monkeypatch):
    _claude(home)
    _write_tiers(home, "{broken")
    out = _status(home, monkeypatch)
    assert "drifted  tiers/claude" in out
    assert "invalid" in out
