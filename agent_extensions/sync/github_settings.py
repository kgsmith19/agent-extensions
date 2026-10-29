"""ext#97 dispatch-only GitHub settings apply.

Declared desired state (settings/github-settings.json) is validated fail-closed,
diffed against live state, and applied only when the caller passes --apply.
Stdlib only. Names are printed; values are never printed.
"""

import argparse
import copy
import json
import os
import re
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, TextIO
from urllib.parse import quote

SCHEMA_VERSION = 1

REPO_SETTING_BOOLS = {
    "allow_squash_merge",
    "allow_merge_commit",
    "allow_rebase_merge",
    "allow_auto_merge",
    "delete_branch_on_merge",
    "allow_update_branch",
    "has_issues",
}
REPO_SETTING_STRINGS = {"squash_merge_commit_title", "squash_merge_commit_message"}
REPO_SETTING_KEYS = REPO_SETTING_BOOLS | REPO_SETTING_STRINGS

RULE_TYPES = {
    "pull_request",
    "required_status_checks",
    "required_linear_history",
    "non_fast_forward",
    "deletion",
    "creation",
    "update",
}
RULESET_KEYS = {"name", "target", "enforcement", "conditions", "rules", "bypass_actors"}
ENFORCEMENTS = {"active", "evaluate", "disabled"}
RULE_KEYS = {"type", "parameters"}

LABEL_KEYS = {"name", "color", "description"}
LABEL_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9:._ -]{0,49}$")
COLOR_RE = re.compile(r"^[0-9a-fA-F]{6}$")

VARIABLE_NAME_RE = re.compile(r"^[A-Z][A-Z0-9_]*$")
SECRET_LIKE_SUFFIXES = ("_TOKEN", "_SECRET", "_PASSWORD", "_PAT", "_KEY", "_CREDENTIAL", "_CREDENTIALS")

REPO_BLOCK_KEYS = {"repo", "rulesets", "labels", "variables"}
REPO_SLUG_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
LABEL_PAGE_SIZE = 100


class SchemaError(ValueError):
    """Declared settings failed closed validation."""


@dataclass(frozen=True)
class Change:
    op: str  # create | update | same | extra
    kind: str  # repo | ruleset | label | variable
    name: str


class GitHubApi:
    """Minimal urllib GitHub API client; base URL injectable for tests."""

    def __init__(self, base_url: str, token: str):
        self.base_url = base_url.rstrip("/")
        self.token = token

    def request(self, method: str, path: str, payload: Optional[Dict] = None):
        data = None
        headers = {
            "Accept": "application/vnd.github+json",
            "Authorization": "Bearer %s" % self.token,
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "agent-extensions-settings",
        }
        if payload is not None:
            data = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(self.base_url + path, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request) as response:
                body = response.read().decode("utf-8")
                return response.status, (json.loads(body) if body else None)
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", "replace")
            try:
                parsed = json.loads(body)
            except ValueError:
                parsed = None
            return exc.code, parsed


def _fail(where: str, message: str) -> None:
    raise SchemaError(f"{where}: {message}")


def _check_keys(where: str, obj: Any, allowed: set) -> None:
    if not isinstance(obj, dict):
        _fail(where, "must be an object")
    for key in obj:
        if key not in allowed:
            _fail(where, f"unknown key {key!r}")


def _validate_ruleset(where: str, rs: Any) -> Dict[str, Any]:
    _check_keys(where, rs, RULESET_KEYS)
    for required in ("name", "target", "enforcement", "conditions", "rules"):
        if required not in rs:
            _fail(where, f"missing key {required!r}")
    if not isinstance(rs["name"], str) or not rs["name"]:
        _fail(where, "name must be a non-empty string")
    if rs["target"] != "branch":
        _fail(where, "target must be 'branch'")
    if rs["enforcement"] not in ENFORCEMENTS:
        _fail(where, f"unknown enforcement {rs['enforcement']!r}")
    if not isinstance(rs["conditions"], dict):
        _fail(where, "conditions must be an object")
    if not isinstance(rs["rules"], list):
        _fail(where, "rules must be a list")
    for index, rule in enumerate(rs["rules"]):
        _check_keys(f"{where} rules[{index}]", rule, RULE_KEYS)
        if rule.get("type") not in RULE_TYPES:
            _fail(f"{where} rules[{index}]", f"unknown rule type {rule.get('type')!r}")
    bypass = rs.get("bypass_actors", [])
    if not isinstance(bypass, list):
        _fail(where, "bypass_actors must be a list")
    normalized = copy.deepcopy(rs)
    normalized["bypass_actors"] = bypass
    return normalized


def _validate_label(where: str, label: Any) -> Dict[str, Any]:
    _check_keys(where, label, LABEL_KEYS)
    if "name" not in label or "color" not in label:
        _fail(where, "label needs name and color")
    if not isinstance(label["name"], str) or not LABEL_NAME_RE.match(label["name"]):
        _fail(where, "label name has disallowed characters")
    if not isinstance(label["color"], str) or not COLOR_RE.match(label["color"]):
        _fail(where, f"label color {label['color']!r} must be 6 hex digits")
    out = {"name": label["name"], "color": label["color"]}
    if "description" in label:
        if not isinstance(label["description"], str):
            _fail(where, "label description must be a string")
        out["description"] = label["description"]
    return out


def _validate_block(where: str, block: Any) -> Dict[str, Any]:
    _check_keys(where, block, REPO_BLOCK_KEYS)
    out: Dict[str, Any] = {}

    repo = block.get("repo", {})
    _check_keys(f"{where} repo", repo, REPO_SETTING_KEYS)
    for key, value in repo.items():
        if key in REPO_SETTING_BOOLS and not isinstance(value, bool):
            _fail(f"{where} repo", f"{key} must be a boolean")
        if key in REPO_SETTING_STRINGS and not isinstance(value, str):
            _fail(f"{where} repo", f"{key} must be a string")
    out["repo"] = dict(repo)

    rulesets = block.get("rulesets", [])
    if not isinstance(rulesets, list):
        _fail(where, "rulesets must be a list")
    out["rulesets"] = [_validate_ruleset(f"{where} rulesets[{i}]", rs) for i, rs in enumerate(rulesets)]

    labels = block.get("labels", [])
    if not isinstance(labels, list):
        _fail(where, "labels must be a list")
    out["labels"] = [_validate_label(f"{where} labels[{i}]", label) for i, label in enumerate(labels)]

    variables = block.get("variables", {})
    if not isinstance(variables, dict):
        _fail(where, "variables must be an object")
    for name, value in variables.items():
        if not VARIABLE_NAME_RE.match(name):
            _fail(where, f"variable name {name!r} is not an Actions variable name")
        if name.endswith(SECRET_LIKE_SUFFIXES):
            _fail(where, f"variable name {name!r} looks secret; secrets are not variables")
        if not isinstance(value, str):
            _fail(where, f"variable {name} value must be a string")
    out["variables"] = dict(variables)
    return out


def validate(doc: Any) -> Dict[str, Any]:
    """Fail-closed schema check. Returns a normalized copy with defaults filled."""
    _check_keys("settings", doc, {"schema_version", "repos"})
    if doc.get("schema_version") != SCHEMA_VERSION or type(doc.get("schema_version")) is not int:
        _fail("settings", "schema_version must be 1")
    if not isinstance(doc.get("repos"), dict):
        _fail("settings", "repos must be an object")
    repos: Dict[str, Any] = {}
    for slug, block in doc["repos"].items():
        if not REPO_SLUG_RE.match(slug):
            _fail("settings", f"repo key {slug!r} must be OWNER/NAME")
        repos[slug] = _validate_block(f"repos[{slug}]", block)
    return {"schema_version": SCHEMA_VERSION, "repos": repos}


def _project_ruleset(rs: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "name": rs.get("name"),
        "target": rs.get("target"),
        "enforcement": rs.get("enforcement"),
        "conditions": rs.get("conditions", {}),
        "rules": rs.get("rules", []),
        "bypass_actors": rs.get("bypass_actors", []),
    }


def _ruleset_body(rs: Dict[str, Any]) -> Dict[str, Any]:
    return _project_ruleset(rs)


def _check_status(method: str, path: str, status: int) -> None:
    if status >= 300:
        raise RuntimeError(f"{method} {path.split('?')[0]} failed with HTTP {status}")


def _paged(api: Any, path: str) -> List[Any]:
    items: List[Any] = []
    page = 1
    while True:
        sep = "&" if "?" in path else "?"
        full = f"{path}{sep}per_page={LABEL_PAGE_SIZE}&page={page}"
        status, body = api.request("GET", full)
        _check_status("GET", full, status)
        batch = body or []
        items.extend(batch)
        if len(batch) < LABEL_PAGE_SIZE:
            return items
        page += 1


def read_live(api: Any, repo: str) -> Dict[str, Any]:
    base = f"/repos/{repo}"

    status, repo_body = api.request("GET", base)
    _check_status("GET", base, status)
    repo_live = {key: repo_body[key] for key in REPO_SETTING_KEYS if key in repo_body}

    rulesets = []
    status, listing = api.request("GET", f"{base}/rulesets")
    _check_status("GET", f"{base}/rulesets", status)
    for summary in listing or []:
        path = f"{base}/rulesets/{summary['id']}"
        status, detail = api.request("GET", path)
        _check_status("GET", path, status)
        rulesets.append(detail)

    labels = [
        {"name": item["name"], "color": item["color"], "description": item.get("description") or ""}
        for item in _paged(api, f"{base}/labels")
    ]

    variables: Dict[str, str] = {}
    status, body = api.request("GET", f"{base}/actions/variables?per_page={LABEL_PAGE_SIZE}")
    _check_status("GET", f"{base}/actions/variables", status)
    for item in (body or {}).get("variables", []):
        variables[item["name"]] = item["value"]

    return {"repo": repo_live, "rulesets": rulesets, "labels": labels, "variables": variables}


def plan(declared: Dict[str, Any], live: Dict[str, Any]) -> List[Change]:
    changes: List[Change] = []

    live_repo = live.get("repo", {})
    for key, want in declared.get("repo", {}).items():
        op = "same" if live_repo.get(key) == want else "update"
        changes.append(Change(op, "repo", key))

    live_rs = {rs["name"]: rs for rs in live.get("rulesets", [])}
    declared_rs_names = set()
    for rs in declared.get("rulesets", []):
        declared_rs_names.add(rs["name"])
        have = live_rs.get(rs["name"])
        if have is None:
            op = "create"
        elif _project_ruleset(have) == _project_ruleset(rs):
            op = "same"
        else:
            op = "update"
        changes.append(Change(op, "ruleset", rs["name"]))
    for name in live_rs:
        if name not in declared_rs_names:
            changes.append(Change("extra", "ruleset", name))

    live_labels = {label["name"].casefold(): label for label in live.get("labels", [])}
    declared_label_keys = set()
    for label in declared.get("labels", []):
        key = label["name"].casefold()
        declared_label_keys.add(key)
        have = live_labels.get(key)
        if have is None:
            op = "create"
        elif (
            have["color"].lower() == label["color"].lower()
            and ("description" not in label
                 or (have.get("description") or "") == label["description"])
        ):
            op = "same"
        else:
            op = "update"
        changes.append(Change(op, "label", label["name"]))
    for key, have in live_labels.items():
        if key not in declared_label_keys:
            changes.append(Change("extra", "label", have["name"]))

    live_vars = live.get("variables", {})
    for name, value in declared.get("variables", {}).items():
        if name not in live_vars:
            op = "create"
        elif live_vars[name] == value:
            op = "same"
        else:
            op = "update"
        changes.append(Change(op, "variable", name))
    for name in live_vars:
        if name not in declared.get("variables", {}):
            changes.append(Change("extra", "variable", name))

    return changes


def apply_changes(
    api: Any, repo: str, declared: Dict[str, Any], live: Dict[str, Any], changes: List[Change]
) -> None:
    base = f"/repos/{repo}"

    repo_updates = {
        c.name: declared["repo"][c.name] for c in changes if c.kind == "repo" and c.op == "update"
    }
    if repo_updates:
        status, _ = api.request("PATCH", base, repo_updates)
        _check_status("PATCH", base, status)

    declared_rs = {rs["name"]: rs for rs in declared.get("rulesets", [])}
    live_rs = {rs["name"]: rs for rs in live.get("rulesets", [])}
    declared_labels = {label["name"].casefold(): label for label in declared.get("labels", [])}
    live_labels = {label["name"].casefold(): label for label in live.get("labels", [])}

    for c in changes:
        if c.op not in ("create", "update"):
            continue
        if c.kind == "ruleset":
            body = _ruleset_body(declared_rs[c.name])
            if c.op == "create":
                path = f"{base}/rulesets"
                status, _ = api.request("POST", path, body)
            else:
                path = f"{base}/rulesets/{live_rs[c.name]['id']}"
                status, _ = api.request("PUT", path, body)
            _check_status("POST" if c.op == "create" else "PUT", path, status)
        elif c.kind == "label":
            key = c.name.casefold()
            want = declared_labels[key]
            if c.op == "create":
                path = f"{base}/labels"
                status, _ = api.request("POST", path, dict(want))
                _check_status("POST", path, status)
            else:
                path = f"{base}/labels/{quote(live_labels[key]['name'], safe='')}"
                # An undeclared description is left as it is live.
                status, _ = api.request("PATCH", path, {k: v for k, v in want.items() if k != "name"})
                _check_status("PATCH", path, status)
        elif c.kind == "variable":
            value = declared["variables"][c.name]
            if c.op == "create":
                path = f"{base}/actions/variables"
                status, _ = api.request("POST", path, {"name": c.name, "value": value})
                _check_status("POST", path, status)
            else:
                path = f"{base}/actions/variables/{c.name}"
                status, _ = api.request("PATCH", path, {"name": c.name, "value": value})
                _check_status("PATCH", path, status)


def run_sync(api: Any, repo: str, declared: Dict[str, Any], apply: bool) -> List[Change]:
    live = read_live(api, repo)
    changes = plan(declared, live)
    if apply:
        apply_changes(api, repo, declared, live, changes)
    return changes


SYMBOLS = {"create": "+create", "update": "~update", "extra": "?extra"}


def main(argv: Optional[List[str]] = None, api: Any = None, out: Optional[TextIO] = None) -> int:
    out = out if out is not None else sys.stdout
    parser = argparse.ArgumentParser(prog="python -m agent_extensions.sync.github_settings")
    parser.add_argument("--repo", required=True, help="OWNER/NAME")
    parser.add_argument("--settings", required=True, help="path to github-settings.json")
    parser.add_argument("--apply", action="store_true", help="perform writes (default is dry-run)")
    parser.add_argument("--api-base-url", default="https://api.github.com")
    args = parser.parse_args(argv)

    try:
        with open(args.settings, encoding="utf-8") as handle:
            raw = json.load(handle)
    except (OSError, ValueError) as exc:
        print(f"settings unreadable: {type(exc).__name__}", file=out)
        return 2

    try:
        doc = validate(raw)
    except SchemaError as exc:
        print(f"schema error: {exc}", file=out)
        return 2

    block = doc["repos"].get(args.repo)
    if block is None:
        print(f"no settings declared for {args.repo}", file=out)
        return 2

    if api is None:
        token = os.environ.get("GH_TOKEN", "")
        if not token:
            print("::error::GH_TOKEN not set; settings App token is required", file=out)
            return 2
        api = GitHubApi(args.api_base_url, token)

    print(f"mode: {'apply' if args.apply else 'dry-run (no writes)'} repo: {args.repo}", file=out)
    changes = run_sync(api, args.repo, block, apply=args.apply)
    counts = {"create": 0, "update": 0, "extra": 0, "same": 0}
    for c in changes:
        counts[c.op] += 1
        if c.op != "same":
            print(f"{SYMBOLS[c.op]} {c.kind} {c.name}", file=out)
    print(
        f"summary: {counts['create']} create, {counts['update']} update, "
        f"{counts['extra']} extra, {counts['same']} same",
        file=out,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
