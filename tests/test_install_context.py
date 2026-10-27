"""Bounded contexts: `ae context new|list|brief|wire` (issue #87, std#306).

Mirrors std's check_bounded_contexts semantics (tools/standardctl.py) without
importing std. Every test runs in a temp directory; nothing touches the network.
"""
import subprocess
import sys
from pathlib import Path

import pytest

from agent_extensions.install import MANAGED_MD_MARKER
from agent_extensions.install import context as ctx
from agent_extensions.install.context import (
    CONTEXT_WIRING,
    DEFAULT_BRIEF_BUDGET,
    RestrictedYamlError,
    parse_restricted_yaml,
)

REPO = Path(__file__).resolve().parents[1]


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _manifest(name, purpose="One sentence here.", public=("api.md",), depends_on=(), test="pytest -q"):
    lines = [f"name: {name}", f"purpose: {purpose}"]
    if public:
        lines.append("public:")
        lines += [f"  - {p}" for p in public]
    else:
        lines.append("public: []")
    if depends_on:
        lines.append("depends_on:")
        lines += [f"  - {d}" for d in depends_on]
    else:
        lines.append("depends_on: []")
    lines += ["commands:", f"  test: {test}"]
    return "\n".join(lines) + "\n"


def _context(root: Path, folder: str, name: str, *, public=("api.md",), depends_on=(),
             agents="# rules\n", readme="# readme\n", test="pytest -q", purpose="One sentence here."):
    base = root / folder
    _write(base / "context.yaml", _manifest(name, purpose=purpose, public=public,
                                            depends_on=depends_on, test=test))
    if agents is not None:
        _write(base / "AGENTS.md", agents)
    if readme is not None:
        _write(base / "README.md", readme)
    for p in public:
        _write(base / p, f"public file {p}\n")
    return base


def _tree(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    return root


# ---------------------------------------------------------------- parser


def test_parser_reads_template_shaped_manifest():
    text = (
        "# comment line is ignored\n"
        "name: billing\n"
        "purpose: Charges customers; nothing else.\n"
        "public:\n"
        "  - api.md\n"
        "  - schema/out.json\n"
        "depends_on: []\n"
        "invariants:\n"
        "  - \"Never: refund twice\"\n"
        "commands:\n"
        "  test: pytest -q\n"
        "  lint: ruff check .\n"
    )
    data = parse_restricted_yaml(text, "context.yaml")
    assert data["name"] == "billing"
    assert data["purpose"] == "Charges customers; nothing else."
    assert data["public"] == ["api.md", "schema/out.json"]
    assert data["depends_on"] == []
    assert data["invariants"] == ["Never: refund twice"]
    assert data["commands"] == {"test": "pytest -q", "lint": "ruff check ."}


def test_parser_accepts_vendored_template_after_placeholder_substitution():
    template = (ctx.TEMPLATE_DIR / "context.yaml").read_text(encoding="utf-8")
    text = template.replace("<kebab-case-name>", "a").replace(
        "<one sentence, 200 characters or fewer>", "The a context."
    )
    data = parse_restricted_yaml(text, "context.yaml")
    assert data["name"] == "a"
    assert data["depends_on"] == []


@pytest.mark.parametrize(
    "text, fragment",
    [
        ("name:\ta\n", "tabs are not permitted"),
        ("name: a\n   purpose: x\n", "multiple of 2 spaces"),
        ("depends_on: [a, b]\n", "restricted grammar"),
        ("commands: {test: x}\n", "restricted grammar"),
        ("name: &x a\n", "restricted grammar"),
        ("name: *x\n", "restricted grammar"),
        ("purpose: |\n  text\n", "restricted grammar"),
        ("name: 'a'\n", "restricted grammar"),
        ("name: a # note\n", "inline comments are not supported"),
        ("name: a\nname: b\n", "duplicate key"),
        ("commands:\n  test:\n    deep: x\n", "only one nested level"),
        ("public:\n  - a: b\n", "list items must be plain scalars"),
        ("public:\n  -\n", "empty list items are not permitted"),
        ("public:\n  - a\n  commands: x\n", "cannot mix list items and mapping entries"),
        ("purpose: \"a\\nb\"\n", "escape sequences"),
        ("just words\n", "expected 'key: value' or 'key:'"),
        ("- a\n", "list item outside a key"),
        ("public:\n", "opens a block but nothing follows"),
        ("name: a\n    x: y\n", "unexpected indentation"),
        ("name: a\n\tpurpose: b\n", "tabs are not permitted"),
    ],
)
def test_parser_rejects_unsupported_yaml_loudly(text, fragment):
    with pytest.raises(RestrictedYamlError, match=fragment):
        parse_restricted_yaml(text, "context.yaml")


def test_parser_empty_document_is_empty_mapping():
    assert parse_restricted_yaml("\n# only comments\n", "x") == {}


# ---------------------------------------------------------------- vendoring


def test_templates_are_vendored_with_provenance():
    for name in ("AGENTS.md", "README.md", "context.yaml", "context.schema.json", "VENDORED-FROM"):
        assert (ctx.TEMPLATE_DIR / name).is_file(), name
    provenance = (ctx.TEMPLATE_DIR / "VENDORED-FROM").read_text(encoding="utf-8")
    assert "agent-engineering-standard" in provenance
    assert "commit: " in provenance


# ---------------------------------------------------------------- new


def test_new_writes_three_files_and_substitutes_name_and_purpose(tmp_path, capsys):
    target = tmp_path / "a"
    rc = ctx.main(["new", str(target), "--name", "a", "--purpose", "The a context."])
    assert rc == 0
    assert "created" in capsys.readouterr().out
    manifest = (target / "context.yaml").read_text(encoding="utf-8")
    assert "name: a\n" in manifest
    assert "purpose: The a context.\n" in manifest
    assert "<kebab-case-name>" not in manifest
    assert "<one sentence" not in manifest
    assert (target / "AGENTS.md").read_text(encoding="utf-8").startswith("# a context\n")
    assert (target / "README.md").read_text(encoding="utf-8").startswith("# a\n")
    assert "Budget: 120 lines." in (target / "AGENTS.md").read_text(encoding="utf-8")


def test_new_is_idempotent_and_reports_exists(tmp_path, capsys):
    target = tmp_path / "a"
    ctx.main(["new", str(target), "--name", "a", "--purpose", "The a context."])
    before = {n: (target / n).read_bytes() for n in ("context.yaml", "AGENTS.md", "README.md")}
    capsys.readouterr()
    rc = ctx.main(["new", str(target), "--name", "a", "--purpose", "The a context."])
    out = capsys.readouterr().out
    assert rc == 0
    assert out.count("exists, skipped") == 3
    after = {n: (target / n).read_bytes() for n in before}
    assert before == after


def test_new_never_overwrites_an_existing_file(tmp_path, capsys):
    target = tmp_path / "a"
    _write(target / "README.md", "my own readme\n")
    rc = ctx.main(["new", str(target), "--name", "a", "--purpose", "The a context."])
    out = capsys.readouterr().out
    assert rc == 0
    assert (target / "README.md").read_text(encoding="utf-8") == "my own readme\n"
    assert "exists, skipped" in out
    assert (target / "context.yaml").exists()
    assert (target / "AGENTS.md").exists()


def test_new_creates_nested_folders(tmp_path):
    target = tmp_path / "deep" / "nested" / "a"
    assert ctx.main(["new", str(target), "--name", "a", "--purpose", "The a context."]) == 0
    assert (target / "context.yaml").is_file()


@pytest.mark.parametrize("bad", ["Bad_Name", "-a", "a-", "a--b", "A", "9lives", "a b"])
def test_new_rejects_non_kebab_case_name_and_writes_nothing(tmp_path, capsys, bad):
    target = tmp_path / "a"
    rc = ctx.main(["new", str(target), f"--name={bad}", "--purpose", "The a context."])
    assert rc == 1
    assert "kebab-case" in capsys.readouterr().err
    assert not target.exists()


@pytest.mark.parametrize(
    "purpose",
    ["The a # context.", "Two sentences. Really.", "x" * 201, "", "line one\nname: evil"],
)
def test_new_rejects_purpose_that_breaks_the_manifest(tmp_path, purpose):
    target = tmp_path / "a"
    assert ctx.main(["new", str(target), "--name", "a", "--purpose", purpose]) == 1
    assert not (target / "context.yaml").exists()


# ---------------------------------------------------------------- list


def test_list_valid_tree_prints_edges_and_exits_zero(tmp_path, capsys):
    root = _tree(tmp_path)
    _context(root, "a", "a", public=("api.md",))
    _context(root, "b", "b", public=("api.md",), depends_on=("a",))
    rc = ctx.main(["list", str(root)])
    out = capsys.readouterr().out
    assert rc == 0
    assert "a  path=a  public=[a/api.md]  depends_on=[]" in out
    assert "b  path=b  public=[b/api.md]  depends_on=[a]" in out


def test_list_defaults_to_current_directory(tmp_path, capsys, monkeypatch):
    root = _tree(tmp_path)
    _context(root, "a", "a")
    monkeypatch.chdir(root)
    assert ctx.main(["list"]) == 0
    assert "a  path=a" in capsys.readouterr().out


def test_list_duplicate_names_fail(tmp_path, capsys):
    root = _tree(tmp_path)
    _context(root, "one", "dup")
    _context(root, "two", "dup")
    assert ctx.main(["list", str(root)]) == 1
    assert "duplicate context name 'dup'" in capsys.readouterr().out


def test_list_unknown_dependency_fails(tmp_path, capsys):
    root = _tree(tmp_path)
    _context(root, "b", "b", depends_on=("zz",))
    assert ctx.main(["list", str(root)]) == 1
    assert "depends_on names unknown context 'zz'" in capsys.readouterr().out


def test_list_names_the_dependency_cycle(tmp_path, capsys):
    root = _tree(tmp_path)
    _context(root, "a", "a", depends_on=("b",))
    _context(root, "b", "b", depends_on=("a",))
    assert ctx.main(["list", str(root)]) == 1
    assert "dependency cycle: a -> b -> a" in capsys.readouterr().out


def test_list_three_node_cycle_is_named(tmp_path, capsys):
    root = _tree(tmp_path)
    _context(root, "a", "a", depends_on=("b",))
    _context(root, "b", "b", depends_on=("c",))
    _context(root, "c", "c", depends_on=("a",))
    assert ctx.main(["list", str(root)]) == 1
    assert "dependency cycle: a -> b -> c -> a" in capsys.readouterr().out


def test_list_missing_readme_fails(tmp_path, capsys):
    root = _tree(tmp_path)
    _context(root, "a", "a", readme=None)
    assert ctx.main(["list", str(root)]) == 1
    assert "context 'a' is missing README.md" in capsys.readouterr().out


def test_list_missing_agents_fails(tmp_path, capsys):
    root = _tree(tmp_path)
    _context(root, "a", "a", agents=None)
    assert ctx.main(["list", str(root)]) == 1
    assert "context 'a' is missing AGENTS.md" in capsys.readouterr().out


def test_list_invalid_manifest_unknown_key_fails(tmp_path, capsys):
    root = _tree(tmp_path)
    base = _context(root, "a", "a")
    _write(base / "context.yaml", _manifest("a") + "colour: blue\n")
    assert ctx.main(["list", str(root)]) == 1
    assert "unknown key 'colour'" in capsys.readouterr().out


def test_list_unparseable_manifest_fails(tmp_path, capsys):
    root = _tree(tmp_path)
    base = _context(root, "a", "a")
    _write(base / "context.yaml", "name: a\ndepends_on: [b]\n")
    assert ctx.main(["list", str(root)]) == 1
    assert "manifest is unparseable" in capsys.readouterr().out


def test_list_over_budget_agents_fails(tmp_path, capsys):
    root = _tree(tmp_path)
    _context(root, "a", "a", agents="line\n" * 121)
    assert ctx.main(["list", str(root)]) == 1
    assert "exceeds the context budget of 120" in capsys.readouterr().out


def test_list_public_path_must_exist(tmp_path, capsys):
    root = _tree(tmp_path)
    base = _context(root, "a", "a", public=("api.md",))
    (base / "api.md").unlink()
    assert ctx.main(["list", str(root)]) == 1
    assert "public path 'api.md' does not exist inside the context folder" in capsys.readouterr().out


def test_list_nested_context_must_be_declared_in_children(tmp_path, capsys):
    root = _tree(tmp_path)
    _context(root, "parent", "parent")
    _context(root, "parent/child", "child")
    assert ctx.main(["list", str(root)]) == 1
    assert "nested inside context 'parent'" in capsys.readouterr().out


def test_list_declared_child_passes(tmp_path, capsys):
    root = _tree(tmp_path)
    base = _context(root, "parent", "parent")
    _write(base / "context.yaml", _manifest("parent") + "children:\n  - child\n")
    _context(root, "parent/child", "child")
    assert ctx.main(["list", str(root)]) == 0


def test_list_children_entry_must_be_a_context(tmp_path, capsys):
    root = _tree(tmp_path)
    base = _context(root, "parent", "parent")
    _write(base / "context.yaml", _manifest("parent") + "children:\n  - ghost\n")
    assert ctx.main(["list", str(root)]) == 1
    assert "children entry 'ghost' is not a context folder" in capsys.readouterr().out


@pytest.mark.parametrize(
    "skipped",
    [".git", ".worktrees", "node_modules", "__pycache__", ".evidence"],
)
def test_list_skips_repository_housekeeping_dirs(tmp_path, capsys, skipped):
    root = _tree(tmp_path)
    _context(root, "a", "a")
    # A duplicate name inside a skipped dir would fail if it were scanned.
    _context(root, f"{skipped}/x", "a")
    assert ctx.main(["list", str(root)]) == 0
    out = capsys.readouterr().out
    assert skipped not in out


def test_list_skips_vendored_template_dir(tmp_path, capsys):
    root = _tree(tmp_path)
    _context(root, "a", "a")
    vendored = root / "agent_extensions" / "install" / "templates" / "context"
    _write(vendored / "context.yaml", "name: <kebab-case-name>\n")
    assert ctx.main(["list", str(root)]) == 0


def test_list_of_the_real_vendored_template_dir_is_clean_when_scanned_as_root(tmp_path):
    # Scanning the vendored template directory itself must not report it as a
    # context: its placeholders are not a live manifest.
    records, findings = ctx.check_contexts(ctx.TEMPLATE_DIR)
    assert records == []
    assert findings == []


# ---------------------------------------------------------------- brief


def _brief_tree(tmp_path, *, a_agents=None, b_agents=None):
    root = _tree(tmp_path)
    a_agents = a_agents or "# a rules\nSECRET-A-RULES\n"
    b_agents = b_agents or (
        "# b rules\n\nRoot rules: `../AGENTS.md`, `../AGENTS/contexts.md`.\n"
    )
    _context(root, "a", "a", public=("api.md", "docs/guide.md"), agents=a_agents,
             readme="SECRET-A-README\n")
    _context(root, "b", "b", public=("api.md",), depends_on=("a",), agents=b_agents,
             readme="# b\nB-README-BODY\n", test="pytest -q tests/test_b.py")
    return root


def test_brief_packs_context_files_verbatim_and_commands(tmp_path, capsys):
    root = _brief_tree(tmp_path)
    rc = ctx.main(["brief", str(root / "b"), "--root", str(root)])
    out = capsys.readouterr().out
    assert rc == 0
    assert "B-README-BODY" in out
    assert "# b rules" in out
    assert "name: b\n" in out
    assert "- test: pytest -q tests/test_b.py" in out


def test_brief_is_deterministic_and_reports_byte_size(tmp_path, capsys):
    root = _brief_tree(tmp_path)
    ctx.main(["brief", str(root / "b"), "--root", str(root)])
    first = capsys.readouterr().out
    ctx.main(["brief", str(root / "b"), "--root", str(root)])
    second = capsys.readouterr().out
    assert first == second
    last = first.rstrip("\n").splitlines()[-1]
    assert last.startswith("pack bytes: ")
    size = int(last.split(": ", 1)[1])
    pack = first[: first.rindex("pack bytes: ")]
    assert size == len(pack.encode("utf-8"))


def test_brief_includes_dependency_public_paths_but_never_contents(tmp_path, capsys):
    root = _brief_tree(tmp_path)
    assert ctx.main(["brief", str(root / "b"), "--root", str(root)]) == 0
    out = capsys.readouterr().out
    assert "a/api.md" in out
    assert "a/docs/guide.md" in out
    assert "SECRET-A-README" not in out
    assert "SECRET-A-RULES" not in out
    assert "public file api.md" not in out


def test_brief_includes_root_rule_anchor_lines(tmp_path, capsys):
    root = _brief_tree(tmp_path)
    ctx.main(["brief", str(root / "b"), "--root", str(root)])
    out = capsys.readouterr().out
    assert "Root rules: `../AGENTS.md`, `../AGENTS/contexts.md`." in out


def test_brief_over_budget_fails_and_prints_no_pack(tmp_path, capsys):
    root = _brief_tree(tmp_path)
    rc = ctx.main(["brief", str(root / "b"), "--root", str(root), "--budget-bytes", "50"])
    captured = capsys.readouterr()
    assert rc == 1
    assert captured.out == ""
    assert "over budget" in captured.err


def test_brief_default_budget_is_24576_bytes():
    assert DEFAULT_BRIEF_BUDGET == 24576


def test_brief_at_exact_budget_passes(tmp_path, capsys):
    root = _brief_tree(tmp_path)
    ctx.main(["brief", str(root / "b"), "--root", str(root)])
    size = int(capsys.readouterr().out.rstrip("\n").splitlines()[-1].split(": ", 1)[1])
    assert ctx.main(["brief", str(root / "b"), "--root", str(root), "--budget-bytes", str(size)]) == 0


def test_brief_refuses_an_invalid_tree(tmp_path, capsys):
    root = _brief_tree(tmp_path)
    _context(root, "c", "c", depends_on=("missing",))
    rc = ctx.main(["brief", str(root / "b"), "--root", str(root)])
    captured = capsys.readouterr()
    assert rc == 1
    assert captured.out == ""
    assert "depends_on names unknown context 'missing'" in captured.err


def test_brief_rejects_a_folder_without_manifest(tmp_path, capsys):
    root = _brief_tree(tmp_path)
    _write(root / "plain" / "notes.txt", "x\n")
    assert ctx.main(["brief", str(root / "plain"), "--root", str(root)]) == 1


# ---------------------------------------------------------------- wire


def test_wire_renders_managed_claude_md_beside_each_agents_md(tmp_path, capsys):
    root = _brief_tree(tmp_path)
    rc = ctx.main(["wire", str(root)])
    assert rc == 0
    expected = f"{MANAGED_MD_MARKER}\n@AGENTS.md\n"
    for folder in ("a", "b"):
        assert (root / folder / "CLAUDE.md").read_text(encoding="utf-8") == expected
    assert "created" in capsys.readouterr().out


def test_wire_is_idempotent(tmp_path, capsys):
    root = _brief_tree(tmp_path)
    ctx.main(["wire", str(root)])
    before = (root / "a" / "CLAUDE.md").read_bytes()
    capsys.readouterr()
    assert ctx.main(["wire", str(root)]) == 0
    out = capsys.readouterr().out
    assert "applied" in out
    assert "created" not in out
    assert (root / "a" / "CLAUDE.md").read_bytes() == before


def test_wire_rewrites_a_drifted_managed_file(tmp_path):
    root = _brief_tree(tmp_path)
    _write(root / "a" / "CLAUDE.md", f"{MANAGED_MD_MARKER}\nold body\n")
    assert ctx.main(["wire", str(root)]) == 0
    assert (root / "a" / "CLAUDE.md").read_text(encoding="utf-8") == f"{MANAGED_MD_MARKER}\n@AGENTS.md\n"


def test_wire_skips_unmanaged_claude_md(tmp_path, capsys):
    root = _brief_tree(tmp_path)
    _write(root / "a" / "CLAUDE.md", "my personal notes\n")
    rc = ctx.main(["wire", str(root)])
    out = capsys.readouterr().out
    assert rc == 1
    assert "skipped-unmanaged" in out
    assert (root / "a" / "CLAUDE.md").read_text(encoding="utf-8") == "my personal notes\n"
    assert (root / "b" / "CLAUDE.md").exists()


def test_wire_prints_harness_support_and_names_gaps(tmp_path, capsys):
    root = _brief_tree(tmp_path)
    ctx.main(["wire", str(root)])
    out = capsys.readouterr().out
    assert "claude-code  rendered: <context>/CLAUDE.md -> @AGENTS.md" in out
    assert "codex  native: reads nearest AGENTS.md" in out
    for gap in ("gemini", "kilo", "pi", "cline", "opencode", "generic"):
        assert f"{gap}  gap: " in out
    assert "gaps: gemini, kilo, pi, cline, opencode, generic" in out


def test_wire_refuses_to_write_on_findings(tmp_path, capsys):
    root = _brief_tree(tmp_path)
    _context(root, "c", "c", depends_on=("missing",))
    assert ctx.main(["wire", str(root)]) == 1
    assert not (root / "a" / "CLAUDE.md").exists()


def test_context_wiring_declares_every_harness():
    assert set(CONTEXT_WIRING) == {
        "claude-code", "codex", "gemini", "kilo", "pi", "cline", "opencode", "generic",
    }
    assert CONTEXT_WIRING["claude-code"].startswith("rendered: ")
    assert CONTEXT_WIRING["codex"].startswith("native: ")
    for name, state in CONTEXT_WIRING.items():
        if name not in ("claude-code", "codex"):
            assert state.startswith("gap: ") and len(state) > len("gap: "), name


# ---------------------------------------------------------------- CLI wiring


def test_install_module_dispatches_context_subcommand(tmp_path):
    root = _tree(tmp_path)
    _context(root, "a", "a")
    proc = subprocess.run(
        [sys.executable, "-m", "agent_extensions.install", "context", "list", str(root)],
        cwd=str(tmp_path),
        env={"PYTHONPATH": str(REPO), "PATH": "/usr/bin:/bin"},
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode == 0, proc.stderr
    assert "a  path=a" in proc.stdout
