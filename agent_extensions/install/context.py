"""Bounded contexts for agentic DDD: `ae context new|list|brief|wire` (issue #87).

A context is a folder holding context.yaml, AGENTS.md and README.md (std#306,
AGENTS/contexts.md). The rules here mirror std's check_bounded_contexts and its
helpers in tools/standardctl.py; std is never imported at runtime. Stdlib only:
context.yaml is read by parse_restricted_yaml, which accepts exactly the
manifest shape and raises RestrictedYamlError on anything else.

    ae context new <path> --name N --purpose P   scaffold from vendored templates
    ae context list [root]                       inventory + check (exit 1 on findings)
    ae context brief <path> [--root R] [--budget-bytes N]
                                                 deterministic pack for one context
    ae context wire [root]                       managed CLAUDE.md -> @AGENTS.md

Exit 0 on success, 1 on any finding or refusal. Output and findings go to
stdout except brief's refusal messages, which go to stderr.
"""

from __future__ import annotations

import argparse
import os
import posixpath
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, NamedTuple, Optional, Tuple

from agent_extensions.install import MANAGED_MD_MARKER

TEMPLATE_DIR = Path(__file__).resolve().parent / "templates" / "context"
VENDORED_REL = "agent_extensions/install/templates/context"
# std's adopter skeleton (std#306): placeholders, never a live context. Mirrors
# standardctl.py CONTEXT_TEMPLATE_DIR; matched on the repo-relative manifest path.
STD_TEMPLATE_PREFIX = "TEMPLATES/context/"
MANIFEST = "context.yaml"
AGENTS = "AGENTS.md"
README = "README.md"
CLAUDE_MD = "CLAUDE.md"
REQUIRED_KEYS = ("name", "purpose", "public", "depends_on", "commands")
OPTIONAL_KEYS = ("invariants", "owners", "children")
COMMAND_KEYS = ("test", "lint", "build")
NAME_PATTERN = r"^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$"
PURPOSE_MAX = 200
AGENTS_MAX_LINES = 120
DEFAULT_BRIEF_BUDGET = 24576
SKIP_SEGMENTS = (".git", ".worktrees", "node_modules", "__pycache__", ".evidence")

# Harness support for nested context wiring. claude-code is rendered by `wire`;
# codex reads the nearest AGENTS.md natively; every other harness is a named gap.
CONTEXT_WIRING: Dict[str, str] = {
    "claude-code": "rendered: <context>/CLAUDE.md -> @AGENTS.md",
    "codex": "native: reads nearest AGENTS.md",
    "gemini": "gap: Gemini CLI reads GEMINI.md; the installer renders only the global GEMINI.md, no per-context file",
    "kilo": "gap: kilo integration is the kilo-HARNESS.md shim only; no nested per-context rules surface is verified",
    "pi": "gap: pi integration is the pi-AGENTS.md global rules only; no nested per-context rules surface is verified",
    "cline": "gap: no cline instruction surface in this repo; nothing rendered",
    "opencode": "gap: no opencode instruction surface in this repo; nothing rendered",
    "generic": "gap: no harness-neutral nested-instruction surface; nothing rendered",
}

_NAME_RE = re.compile(NAME_PATTERN)
_SECOND_SENTENCE_RE = re.compile(r"[.!?] +[A-Z]")
_BARE_KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_.-]*$")
_LOOKS_LIKE_KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_.-]*:( |$)")
_DIGITS_RE = re.compile(r"[0-9]+")
_ROOT_LINK_RE = re.compile(r"\.\./AGENTS|AGENTS/")
# Leading characters that start YAML syntax outside the restricted grammar.
_FORBIDDEN_LEADS = ("&", "*", "|", ">", "{", "}", "[", "]", "%", "!", "`", "'", "@", "?")


class RestrictedYamlError(ValueError):
    """Input outside the restricted context.yaml grammar."""


class Finding(NamedTuple):
    path: str
    message: str


# --------------------------------------------------------------------------- parser


def _err(origin: str, lineno: int, message: str) -> RestrictedYamlError:
    return RestrictedYamlError("%s line %d: %s" % (origin, lineno, message))


def _is_list_item(content: str) -> bool:
    return content == "-" or content.startswith("- ")


def _split_entry(content: str, origin: str, lineno: int) -> Tuple[str, str]:
    key, sep, rest = content.partition(":")
    if not sep:
        raise _err(origin, lineno, "expected 'key: value' or 'key:'")
    key = key.strip()
    if not _BARE_KEY_RE.match(key):
        raise _err(origin, lineno, "invalid key %r" % key)
    return key, rest.strip()


def _scalar(raw: str, origin: str, lineno: int) -> Any:
    value = raw.strip()
    if value == "[]":
        return []
    if value == "-" or value.startswith("- "):
        raise _err(origin, lineno, "list items must be on their own lines under the key")
    if value.startswith('"'):
        if len(value) < 2 or not value.endswith('"'):
            raise _err(origin, lineno, "unterminated quoted string")
        inner = value[1:-1]
        if '"' in inner or "\\" in inner:
            raise _err(origin, lineno, "escape sequences and embedded quotes are not supported")
        return inner
    if value[:1] in _FORBIDDEN_LEADS or value.startswith("["):
        raise _err(
            origin, lineno,
            "anchors, aliases, flow collections, tags, single quotes and block scalars "
            "are outside the restricted grammar: %r" % value,
        )
    if "#" in value:
        raise _err(origin, lineno, "inline comments are not supported: %r" % value)
    if _DIGITS_RE.fullmatch(value):
        return int(value)
    return value


def _parse_block(tokens: list, i: int, origin: str) -> Tuple[Any, int]:
    """Parse the indent-2 block starting at tokens[i]: a list of scalars or a
    flat mapping of scalars (one nested level only)."""
    is_list = _is_list_item(tokens[i][1])
    items: list = []
    mapping: dict = {}
    while i < len(tokens) and tokens[i][0] == 2:
        _, content, lineno = tokens[i]
        if _is_list_item(content) != is_list:
            raise _err(origin, lineno, "cannot mix list items and mapping entries in one block")
        if is_list:
            if content == "-":
                raise _err(origin, lineno, "empty list items are not permitted")
            value = content[2:].strip()
            if _LOOKS_LIKE_KEY_RE.match(value):
                raise _err(origin, lineno, "list items must be plain scalars, not mappings")
            items.append(_scalar(value, origin, lineno))
        else:
            key, value = _split_entry(content, origin, lineno)
            if key in mapping:
                raise _err(origin, lineno, "duplicate key %r" % key)
            if value == "":
                raise _err(origin, lineno, "only one nested level is permitted under %r" % key)
            mapping[key] = _scalar(value, origin, lineno)
        i += 1
    if i < len(tokens) and tokens[i][0] > 2:
        raise _err(origin, tokens[i][2], "unexpected indentation")
    return (items if is_list else mapping), i


def parse_restricted_yaml(text: str, origin: str = "<string>") -> Dict[str, Any]:
    """Parse a context.yaml in the restricted grammar.

    Accepted: `#` comment lines; top-level `key: scalar`; `key:` followed by an
    indent-2 block of `- scalar` items or of flat `key: scalar` entries; scalars
    are bare (no inline `#`), double-quoted (no escapes), digits, or `[]`.
    Everything else raises RestrictedYamlError naming the line.
    """
    tokens: List[Tuple[int, str, int]] = []
    for lineno, raw in enumerate(text.splitlines(), 1):
        if "\t" in raw:
            raise _err(origin, lineno, "tabs are not permitted")
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            continue
        indent = len(raw) - len(raw.lstrip(" "))
        if indent % 2:
            raise _err(origin, lineno, "indentation must be a multiple of 2 spaces")
        tokens.append((indent, stripped, lineno))

    out: Dict[str, Any] = {}
    i = 0
    while i < len(tokens):
        indent, content, lineno = tokens[i]
        if indent != 0:
            raise _err(origin, lineno, "unexpected indentation")
        if _is_list_item(content):
            raise _err(origin, lineno, "list item outside a key")
        key, value = _split_entry(content, origin, lineno)
        if key in out:
            raise _err(origin, lineno, "duplicate key %r" % key)
        i += 1
        if value == "":
            if i >= len(tokens) or tokens[i][0] == 0:
                raise _err(origin, lineno, "key %r opens a block but nothing follows" % key)
            if tokens[i][0] != 2:
                raise _err(origin, tokens[i][2], "nested blocks must be indented exactly 2 spaces")
            out[key], i = _parse_block(tokens, i, origin)
        else:
            out[key] = _scalar(value, origin, lineno)
    return out


# --------------------------------------------------------------------------- rules


def manifest_violations(data: Any) -> List[str]:
    """Contract violations for one parsed context.yaml (mirrors std's
    _context_manifest_violations and TEMPLATES/context/context.schema.json)."""
    if not isinstance(data, dict):
        return ["manifest must be a mapping"]
    allowed = set(REQUIRED_KEYS) | set(OPTIONAL_KEYS)
    violations = ["unknown key %r" % key for key in data if key not in allowed]
    violations += ["missing required key %r" % key for key in REQUIRED_KEYS if key not in data]
    if "name" in data:
        name = data["name"]
        if not isinstance(name, str) or not _NAME_RE.match(name):
            violations.append("name must be kebab-case (lowercase letters, digits, hyphens)")
    if "purpose" in data:
        purpose = data["purpose"]
        if not isinstance(purpose, str) or not purpose.strip():
            violations.append("purpose must be a non-empty string")
        elif len(purpose) > PURPOSE_MAX:
            violations.append("purpose exceeds %d characters" % PURPOSE_MAX)
        elif _SECOND_SENTENCE_RE.search(purpose):
            violations.append("purpose must be one sentence")
    for key in ("public", "children"):
        if key in data:
            violations += _string_list(key, data[key], relative=True)
    for key in ("depends_on", "invariants", "owners"):
        if key in data:
            violations += _string_list(key, data[key], relative=False)
    if "commands" in data:
        commands = data["commands"]
        if not isinstance(commands, dict):
            violations.append("commands must be a mapping of test, lint, build")
        else:
            for key, value in commands.items():
                if key not in COMMAND_KEYS:
                    violations.append("unknown commands key %r" % key)
                elif not isinstance(value, str) or not value.strip():
                    violations.append("commands.%s must be a non-empty string" % key)
    return violations


def _string_list(key: str, value: Any, relative: bool) -> List[str]:
    if not isinstance(value, list):
        return ["%s must be a list of strings" % key]
    out = []
    for item in value:
        if not isinstance(item, str) or not item.strip():
            out.append("%s entries must be non-empty strings" % key)
        elif relative and (item.startswith("/") or ".." in item.split("/")):
            out.append("%s entry %r must be a relative path inside the context folder" % (key, item))
    return out


def _join(folder: str, rel: str) -> str:
    return posixpath.normpath(posixpath.join(folder, rel))


def _is_vendored(root: Path, folder: str) -> bool:
    """The vendored adopter skeleton carries placeholders and is never a live context."""
    if posixpath.normpath(folder) == VENDORED_REL:
        return True
    return os.path.realpath(root / folder) == os.path.realpath(TEMPLATE_DIR)


def _manifest_paths(root: Path) -> List[str]:
    found = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_SEGMENTS)
        if MANIFEST in filenames:
            rel_dir = os.path.relpath(dirpath, root)
            rel = MANIFEST if rel_dir == "." else posixpath.join(Path(rel_dir).as_posix(), MANIFEST)
            found.append(rel)
    return sorted(found)


def scan_contexts(root: Path) -> Tuple[List[Dict[str, Any]], List[Finding]]:
    """Parse every context.yaml under root. Returns the records of manifests that
    satisfy the contract plus one finding per violation."""
    records: List[Dict[str, Any]] = []
    findings: List[Finding] = []
    for rel in _manifest_paths(root):
        folder = posixpath.dirname(rel) or "."
        if rel.startswith(STD_TEMPLATE_PREFIX) or _is_vendored(root, folder):
            continue
        try:
            data = parse_restricted_yaml((root / rel).read_text(encoding="utf-8"), rel)
        except RestrictedYamlError as exc:
            findings.append(Finding(rel, "manifest is unparseable: %s" % exc))
            continue
        except UnicodeDecodeError:
            findings.append(Finding(rel, "manifest is not UTF-8 text"))
            continue
        violations = manifest_violations(data)
        if violations:
            findings.extend(Finding(rel, violation) for violation in violations)
            continue
        records.append({
            "name": data["name"],
            "folder": folder,
            "manifest": rel,
            "purpose": data["purpose"],
            "public": list(data["public"]),
            "depends_on": list(data["depends_on"]),
            "children": [posixpath.normpath(child) for child in data.get("children", [])],
            "commands": dict(data["commands"]),
        })
    return records, findings


def dependency_cycle_findings(known: Dict[str, Dict[str, Any]]) -> List[Finding]:
    """DFS over depends_on edges; each distinct cycle is named once."""
    findings: List[Finding] = []
    state: Dict[str, int] = {}  # 1 = on the DFS stack, 2 = finished
    reported = set()

    def visit(name: str, stack: List[str]) -> None:
        state[name] = 1
        stack.append(name)
        for dep in known[name]["depends_on"]:
            if dep not in known:
                continue
            if state.get(dep) == 1:
                cycle = stack[stack.index(dep):] + [dep]
                key = frozenset(cycle)
                if key not in reported:
                    reported.add(key)
                    findings.append(Finding(
                        known[name]["manifest"],
                        "dependency cycle: %s" % " -> ".join(cycle),
                    ))
            elif dep not in state:
                visit(dep, stack)
        stack.pop()
        state[name] = 2

    for name in sorted(known):
        if name not in state:
            visit(name, [])
    return findings


def nesting_findings(records: List[Dict[str, Any]]) -> List[Finding]:
    """A context folder inside another must be listed in the parent's children;
    every listed child must be a context folder."""
    findings: List[Finding] = []
    folders = {record["folder"]: record for record in records}

    def within(child: str, parent: str) -> bool:
        return parent == "." or child == parent or child.startswith(parent + "/")

    for record in records:
        ancestors = [
            folders[folder] for folder in folders
            if folder != record["folder"] and within(record["folder"], folder)
        ]
        if ancestors:
            parent = max(ancestors, key=lambda rec: len(rec["folder"]))
            rel = posixpath.relpath(record["folder"], parent["folder"])
            if rel not in parent["children"]:
                findings.append(Finding(
                    record["manifest"],
                    "context %r is nested inside context %r but %s does not list %r in children"
                    % (record["name"], parent["name"], parent["manifest"], rel),
                ))
        for child in record["children"]:
            if _join(record["folder"], child) not in folders:
                findings.append(Finding(
                    record["manifest"],
                    "children entry %r is not a context folder" % child,
                ))
    return findings


def check_contexts(root: Path) -> Tuple[List[Dict[str, Any]], List[Finding]]:
    """Full bounded-context check (mirrors std's check_bounded_contexts)."""
    records, findings = scan_contexts(root)

    names: Dict[str, List[Dict[str, Any]]] = {}
    for record in records:
        names.setdefault(record["name"], []).append(record)

    for record in records:
        folder = record["folder"]
        for required in (AGENTS, README):
            rel = _join(folder, required)
            if not (root / rel).is_file():
                findings.append(Finding(
                    rel,
                    "context %r is missing %s (required beside context.yaml)"
                    % (record["name"], required),
                ))
        agents_rel = _join(folder, AGENTS)
        if (root / agents_rel).is_file():
            count = len((root / agents_rel).read_text(encoding="utf-8").splitlines())
            if count > AGENTS_MAX_LINES:
                findings.append(Finding(
                    agents_rel,
                    "%d lines exceeds the context budget of %d; split the context rather "
                    "than adding rules (AGENTS/contexts.md)" % (count, AGENTS_MAX_LINES),
                ))
        for public in record["public"]:
            if not (root / _join(folder, public)).exists():
                findings.append(Finding(
                    record["manifest"],
                    "public path %r does not exist inside the context folder" % public,
                ))

    for name, group in sorted(names.items()):
        if len(group) > 1:
            for record in group:
                findings.append(Finding(
                    record["manifest"],
                    "duplicate context name %r (declared by %d contexts)" % (name, len(group)),
                ))
    known = {name: group[0] for name, group in names.items()}
    for record in records:
        for dep in record["depends_on"]:
            if dep not in known:
                findings.append(Finding(
                    record["manifest"], "depends_on names unknown context %r" % dep,
                ))
    findings.extend(dependency_cycle_findings(known))
    findings.extend(nesting_findings(records))
    return records, findings


# --------------------------------------------------------------------------- list


def _listing_lines(records: List[Dict[str, Any]]) -> List[str]:
    lines = []
    for record in sorted(records, key=lambda rec: rec["folder"]):
        folder = record["folder"]
        public = ", ".join(_join(folder, p) for p in record["public"])
        lines.append("%s  path=%s  public=[%s]  depends_on=[%s]" % (
            record["name"], folder, public, ", ".join(record["depends_on"]),
        ))
    return lines


def cmd_list(root: Path) -> int:
    records, findings = check_contexts(root)
    for line in _listing_lines(records):
        print(line)
    for finding in findings:
        print("error  %s: %s" % (finding.path, finding.message))
    if not records and not findings:
        print("no bounded contexts under %s" % root)
    else:
        print("%d context(s), %d finding(s)" % (len(records), len(findings)))
    return 1 if findings else 0


# --------------------------------------------------------------------------- new


_TEMPLATE_FILES = (MANIFEST, AGENTS, README)


def _render(filename: str, name: str, purpose: str) -> str:
    text = (TEMPLATE_DIR / filename).read_text(encoding="utf-8")
    subs = {
        AGENTS: [
            ("# <name> context", "# %s context" % name),
            ("<One sentence, matching the `purpose` in context.yaml.>", purpose),
        ],
        README: [("# <Context name>", "# %s" % name)],
        MANIFEST: [
            ("name: <kebab-case-name>", "name: %s" % name),
            ("purpose: <one sentence, 200 characters or fewer>", "purpose: %s" % purpose),
        ],
    }.get(filename, [])
    for old, new in subs:
        if text.count(old) != 1:
            raise RuntimeError("vendored template %s lacks the placeholder %r" % (filename, old))
        text = text.replace(old, new)
    return text


def cmd_new(folder: Path, name: str, purpose: str) -> int:
    if not _NAME_RE.match(name):
        print("error  name %r must be kebab-case (lowercase letters, digits, single hyphens)" % name,
              file=sys.stderr)
        return 1
    if not purpose or "\n" in purpose or "\r" in purpose:
        print("error  purpose must be one non-empty line", file=sys.stderr)
        return 1
    rendered = {filename: _render(filename, name, purpose) for filename in _TEMPLATE_FILES}
    try:
        violations = manifest_violations(parse_restricted_yaml(rendered[MANIFEST], MANIFEST))
    except RestrictedYamlError as exc:
        print("error  purpose would produce an invalid manifest: %s" % exc, file=sys.stderr)
        return 1
    if violations:
        print("error  purpose would produce an invalid manifest: %s" % "; ".join(violations),
              file=sys.stderr)
        return 1
    try:
        folder.mkdir(parents=True, exist_ok=True)
        for filename in _TEMPLATE_FILES:
            target = folder / filename
            if target.exists():
                print("exists, skipped  %s" % target)
                continue
            target.write_text(rendered[filename], encoding="utf-8")
            print("created  %s" % target)
    except OSError as exc:
        print("error  %s: %s" % (folder, exc), file=sys.stderr)
        return 1
    print("next: fill the remaining <placeholders> (public paths, commands, AGENTS.md rules), "
          "then run `ae context list`")
    return 0


# --------------------------------------------------------------------------- brief


def _nearest_root(target: Path) -> Path:
    for candidate in (target, *target.parents):
        if (candidate / ".git").exists():
            return candidate
    return Path.cwd().resolve()


def _block(title: str, text: str) -> str:
    body = text if text.endswith("\n") else text + "\n"
    return "## %s\n%s" % (title, body)


def _closure(by_name: Dict[str, Dict[str, Any]], start: str) -> List[Dict[str, Any]]:
    """The context named start plus its transitive depends_on closure."""
    seen: List[str] = []
    stack = [start]
    while stack:
        name = stack.pop()
        if name in seen or name not in by_name:
            continue
        seen.append(name)
        stack.extend(by_name[name]["depends_on"])
    return [by_name[name] for name in seen]


def _owned_by(path: str, folders: set) -> bool:
    """True when path is a folder's own file or lies under one (folder "." owns all)."""
    return any(f == "." or path == f or path.startswith(f + "/") for f in folders)


def _relevant(finding: Finding, folders: set, manifests: set, names: set) -> bool:
    """A finding blocks a brief when it touches the closure: a file the closure
    owns, or a tree-level finding (duplicate, cycle, nesting, unknown edge) that
    names a closure member."""
    if finding.path in manifests or _owned_by(finding.path, folders):
        return True
    tokens = set(re.findall(r"'([^']+)'", finding.message))
    prefix = "dependency cycle: "
    if finding.message.startswith(prefix):
        tokens.update(finding.message[len(prefix):].split(" -> "))
    return bool(tokens & names)


def brief_pack(root: Path, folder: str) -> Tuple[str, List[str], List[str]]:
    """Deterministic pack for one context: (pack, problems, warnings).

    problems (non-empty means refuse, pack is empty) are findings that touch the
    target or its transitive depends_on closure; other findings are warnings.
    Dependencies contribute public paths only, never their contents.
    """
    records, findings = check_contexts(root)
    by_folder = {record["folder"]: record for record in records}
    by_name = {record["name"]: record for record in records}
    target = by_folder.get(folder)
    closure = _closure(by_name, target["name"]) if target else []
    folders = {folder} | {rec["folder"] for rec in closure}
    manifests = {_join(folder, MANIFEST)} | {rec["manifest"] for rec in closure}
    names = {rec["name"] for rec in closure}
    problems: List[str] = []
    warnings: List[str] = []
    for finding in findings:
        line = "%s: %s" % (finding.path, finding.message)
        if _relevant(finding, folders, manifests, names):
            problems.append(line)
        else:
            warnings.append(line)
    if problems:
        return "", problems, warnings
    if target is None:
        return "", ["%s: no bounded context here (needs %s)" % (folder, MANIFEST)], warnings

    def read(rel: str) -> str:
        return (root / rel).read_text(encoding="utf-8")

    agents_rel = _join(folder, AGENTS)
    agents_text = read(agents_rel)
    sections = [
        "# context pack: %s\n# folder: %s\n" % (target["name"], folder),
        _block(agents_rel, agents_text),
        _block(_join(folder, README), read(_join(folder, README))),
        _block(_join(folder, MANIFEST), read(_join(folder, MANIFEST))),
    ]
    dep_lines = []
    for dep in target["depends_on"]:
        dep_record = by_name[dep]
        for public in dep_record["public"]:
            dep_lines.append("- %s: %s" % (dep, _join(dep_record["folder"], public)))
        if not dep_record["public"]:
            dep_lines.append("- %s: (no public paths)" % dep)
    if not target["depends_on"]:
        dep_lines.append("(none)")
    sections.append(_block("dependencies (public paths only; contents not included)",
                           "\n".join(dep_lines)))
    command_lines = [
        "- %s: %s" % (key, target["commands"][key])
        for key in COMMAND_KEYS if key in target["commands"]
    ]
    sections.append(_block("scoped commands (run from the repository root)",
                           "\n".join(command_lines) or "(none)"))
    anchors = [line for line in agents_text.splitlines() if _ROOT_LINK_RE.search(line)]
    sections.append(_block("root rule links (AGENTS.md lines linking ../AGENTS or AGENTS/)",
                           "\n".join(anchors) or "(none)"))
    return _join_sections(sections), [], warnings


def _join_sections(sections: List[str]) -> str:
    """Sections are separated by one blank line; every section ends with a newline."""
    return "\n".join(s if s.endswith("\n") else s + "\n" for s in sections)


def cmd_brief(target: Path, root_arg: Optional[str], budget: int) -> int:
    if not target.is_dir():
        print("error  %s is not a directory" % target, file=sys.stderr)
        return 1
    if budget < 1:
        print("error  --budget-bytes must be a positive integer", file=sys.stderr)
        return 1
    root = Path(root_arg).resolve() if root_arg else _nearest_root(target.resolve())
    try:
        folder = posixpath.normpath(target.resolve().relative_to(root).as_posix())
    except ValueError:
        print("error  %s is outside root %s" % (target, root), file=sys.stderr)
        return 1
    pack, problems, warnings = brief_pack(root, folder)
    for warning in warnings:
        print("warning  %s" % warning, file=sys.stderr)
    if problems:
        for problem in problems:
            print("error  %s" % problem, file=sys.stderr)
        print("brief: refusing to pack; fix the findings above", file=sys.stderr)
        return 1
    size = len(pack.encode("utf-8"))
    if size > budget:
        print("brief: pack is %d bytes, over budget (%d bytes); nothing printed. "
              "Split the context rather than truncating (AGENTS/contexts.md)" % (size, budget),
              file=sys.stderr)
        return 1
    sys.stdout.write(pack)
    print("pack bytes: %d" % size)
    return 0


# --------------------------------------------------------------------------- wire


WIRED_TEXT = "%s\n@AGENTS.md\n" % MANAGED_MD_MARKER


def cmd_wire(root: Path) -> int:
    records, findings = check_contexts(root)
    if findings:
        for finding in findings:
            print("error  %s: %s" % (finding.path, finding.message))
        print("wire: refusing to write; fix the findings above")
        return 1
    unmanaged = 0
    for record in sorted(records, key=lambda rec: rec["folder"]):
        rel = _join(record["folder"], CLAUDE_MD)
        target = root / rel
        if not target.exists():
            target.write_text(WIRED_TEXT, encoding="utf-8")
            print("created  %s" % rel)
            continue
        current = target.read_text(encoding="utf-8")
        if current == WIRED_TEXT:
            print("applied  %s: current" % rel)
        elif MANAGED_MD_MARKER in current:
            target.write_text(WIRED_TEXT, encoding="utf-8")
            print("updated  %s: rewrote managed file" % rel)
        else:
            unmanaged += 1
            print("skipped-unmanaged  %s: present, not ours" % rel)
    print("harness support (nested contexts):")
    for harness, state in CONTEXT_WIRING.items():
        print("%s  %s" % (harness, state))
    gaps = [name for name, state in CONTEXT_WIRING.items() if state.startswith("gap:")]
    print("gaps: %s" % ", ".join(gaps))
    return 1 if unmanaged else 0


# --------------------------------------------------------------------------- CLI


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="ae context",
        description="bounded contexts (issue #87, std#306): new, list, brief, wire",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)
    p_new = sub.add_parser("new", help="scaffold a context from the vendored templates (never overwrites)")
    p_new.add_argument("path")
    p_new.add_argument("--name", required=True, help="kebab-case context name")
    p_new.add_argument("--purpose", required=True, help="one sentence, 200 characters or fewer")
    p_list = sub.add_parser("list", help="inventory contexts and run the bounded-context checks")
    p_list.add_argument("root", nargs="?", default=None)
    p_brief = sub.add_parser("brief", help="deterministic text pack for one context")
    p_brief.add_argument("path")
    p_brief.add_argument("--root", default=None, help="repository root (default: nearest .git ancestor)")
    p_brief.add_argument("--budget-bytes", type=int, default=DEFAULT_BRIEF_BUDGET)
    p_wire = sub.add_parser("wire", help="render managed CLAUDE.md -> @AGENTS.md beside each context")
    p_wire.add_argument("root", nargs="?", default=None)
    args = parser.parse_args(argv)

    if args.cmd == "new":
        return cmd_new(Path(args.path), args.name, args.purpose)
    if args.cmd == "brief":
        return cmd_brief(Path(args.path), args.root, args.budget_bytes)
    root = Path(args.root).resolve() if args.root else Path.cwd().resolve()
    if not root.is_dir():
        print("error  %s is not a directory" % root, file=sys.stderr)
        return 1
    if args.cmd == "list":
        return cmd_list(root)
    return cmd_wire(root)


if __name__ == "__main__":
    sys.exit(main())
