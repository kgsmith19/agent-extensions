"""ext#97: dispatch-only GitHub settings apply. Stdlib + pytest only; no network."""

import copy
import io
import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import unquote

import pytest

from agent_extensions.sync import github_settings as gs

REPO_ROOT = Path(__file__).resolve().parents[1]
REPO = "kgsmith19/agent-extensions"
BASE = f"/repos/{REPO}"


class FakeGitHub:
    """Dict-backed stand-in for the REST paths the module uses. Records every call."""

    def __init__(self, repo=None, rulesets=(), labels=(), variables=None):
        self.repo_settings: Dict[str, Any] = dict(repo or {})
        self.rulesets: List[Dict[str, Any]] = [copy.deepcopy(r) for r in rulesets]
        for index, rs in enumerate(self.rulesets):
            rs.setdefault("id", 100 + index)
        self.labels: List[Dict[str, Any]] = [dict(label) for label in labels]
        self.variables: Dict[str, str] = dict(variables or {})
        self.calls: List[Tuple[str, str, Optional[Dict]]] = []
        self._next_id = 500

    def writes(self) -> List[Tuple[str, str, Optional[Dict]]]:
        return [c for c in self.calls if c[0] != "GET"]

    def request(self, method: str, path: str, payload: Optional[Dict] = None):
        self.calls.append((method, path, copy.deepcopy(payload)))
        route, _, _query = path.partition("?")
        if route == BASE:
            if method == "GET":
                return 200, {**self.repo_settings, "full_name": REPO}
            if method == "PATCH":
                self.repo_settings.update(payload or {})
                return 200, {**self.repo_settings}
        if route == f"{BASE}/rulesets":
            if method == "GET":
                return 200, [
                    {"id": r["id"], "name": r["name"], "target": r.get("target", "branch")}
                    for r in self.rulesets
                ]
            if method == "POST":
                self._next_id += 1
                created = {**copy.deepcopy(payload), "id": self._next_id,
                           "node_id": f"RS_{self._next_id}", "updated_at": "2026-10-28T00:00:00Z"}
                self.rulesets.append(created)
                return 201, created
        if route.startswith(f"{BASE}/rulesets/"):
            rid = int(route.rsplit("/", 1)[1])
            found = [r for r in self.rulesets if r["id"] == rid]
            if method == "GET" and found:
                return 200, copy.deepcopy(found[0])
            if method == "PUT" and found:
                found[0].update(copy.deepcopy(payload))
                return 200, copy.deepcopy(found[0])
        if route == f"{BASE}/labels":
            if method == "GET":
                return 200, [dict(label) for label in self.labels]
            if method == "POST":
                self.labels.append(dict(payload))
                return 201, dict(payload)
        if route.startswith(f"{BASE}/labels/"):
            name = unquote(route.rsplit("/", 1)[1])
            found = [label for label in self.labels if label["name"] == name]
            if method == "PATCH" and found:
                found[0].update(payload or {})
                return 200, dict(found[0])
            if method == "DELETE" and found:
                self.labels.remove(found[0])
                return 204, None
        if route == f"{BASE}/actions/variables":
            if method == "GET":
                items = [{"name": k, "value": v} for k, v in self.variables.items()]
                return 200, {"total_count": len(items), "variables": items}
            if method == "POST":
                self.variables[payload["name"]] = payload["value"]
                return 201, None
        if route.startswith(f"{BASE}/actions/variables/"):
            name = route.rsplit("/", 1)[1]
            if method == "PATCH" and name in self.variables:
                self.variables[name] = payload["value"]
                return 204, None
            if method == "DELETE" and name in self.variables:
                del self.variables[name]
                return 204, None
        return 404, {"message": "not found in fake"}


def ruleset(name: str = "main-protection") -> Dict[str, Any]:
    return {
        "name": name,
        "target": "branch",
        "enforcement": "active",
        "conditions": {"ref_name": {"include": ["~DEFAULT_BRANCH"], "exclude": []}},
        "bypass_actors": [],
        "rules": [
            {"type": "pull_request", "parameters": {"required_approving_review_count": 0}},
            {"type": "non_fast_forward"},
        ],
    }


def doc_for(block: Dict[str, Any]) -> Dict[str, Any]:
    return {"schema_version": 1, "repos": {REPO: block}}


def live_view(repo=None, rulesets=(), labels=(), variables=None) -> Dict[str, Any]:
    return {
        "repo": dict(repo or {}),
        "rulesets": [copy.deepcopy(r) for r in rulesets],
        "labels": [dict(label) for label in labels],
        "variables": dict(variables or {}),
    }


def triples(changes) -> List[Tuple[str, str, str]]:
    return [(c.op, c.kind, c.name) for c in changes]


# 1. unknown top-level key fails closed before any API call
def test_schema_rejects_unknown_top_level_key(tmp_path):
    with pytest.raises(gs.SchemaError, match="oops"):
        gs.validate({"schema_version": 1, "repos": {}, "oops": 1})

    settings = tmp_path / "github-settings.json"
    settings.write_text(json.dumps({"schema_version": 1, "repos": {}, "oops": 1}), encoding="utf-8")
    fake = FakeGitHub()
    out = io.StringIO()
    code = gs.main(["--repo", REPO, "--settings", str(settings)], api=fake, out=out)
    assert code != 0
    assert fake.calls == []


# 2. unknown nested repo-settings key (typo) fails closed
def test_schema_rejects_unknown_nested_repo_key():
    block = {"repo": {"allow_merge_commits": False}}
    with pytest.raises(gs.SchemaError, match="allow_merge_commits"):
        gs.validate(doc_for(block))


# 3. unknown ruleset rule type fails closed
def test_schema_rejects_unknown_ruleset_rule_type():
    bad = ruleset()
    bad["rules"] = [{"type": "made_up_rule"}]
    with pytest.raises(gs.SchemaError, match="made_up_rule"):
        gs.validate(doc_for({"rulesets": [bad]}))


# 4. bad label colour and bad label name characters fail closed
def test_schema_rejects_bad_label_color_and_name():
    with pytest.raises(gs.SchemaError, match="zzz"):
        gs.validate(doc_for({"labels": [{"name": "status:ready", "color": "zzz", "description": ""}]}))
    with pytest.raises(gs.SchemaError):
        gs.validate(doc_for({"labels": [{"name": "bad\nname", "color": "0e8a16", "description": ""}]}))


# 5. secret-looking variable names are never accepted as Actions variables
@pytest.mark.parametrize("name", ["DEPLOY_TOKEN", "APP_PRIVATE_KEY", "GH_PAT", "DB_PASSWORD"])
def test_schema_rejects_secret_looking_variable_name(name):
    with pytest.raises(gs.SchemaError, match=name):
        gs.validate(doc_for({"variables": {name: "not-secret"}}))


# 6. schema_version must be exactly 1
def test_schema_requires_schema_version_1():
    with pytest.raises(gs.SchemaError):
        gs.validate({"repos": {}})
    with pytest.raises(gs.SchemaError):
        gs.validate({"schema_version": 2, "repos": {}})


# 7. repo-setting drift is planned as an update
def test_diff_reports_repo_setting_drift():
    declared = {"repo": {"allow_squash_merge": True}}
    changes = gs.plan(declared, live_view(repo={"allow_squash_merge": False}))
    assert ("update", "repo", "allow_squash_merge") in triples(changes)


# 8. label name match is case-insensitive; a missing label is a create
def test_diff_missing_label_is_create_and_match_is_case_insensitive():
    declared = {
        "labels": [
            {"name": "status:ready", "color": "0e8a16", "description": "Ready"},
            {"name": "status:blocked", "color": "b60205", "description": "Blocked"},
        ]
    }
    live = live_view(labels=[{"name": "Status:Ready", "color": "0e8a16", "description": "Ready"}])
    result = triples(gs.plan(declared, live))
    assert ("create", "label", "status:blocked") in result
    assert not any(op == "create" and name == "status:ready" for op, _, name in result)


# 9. ruleset matches by name and ignores server-side metadata
def test_diff_ruleset_match_by_name_ignores_server_metadata():
    declared = {"rulesets": [ruleset()]}
    live_rs = ruleset()
    live_rs.update(
        {
            "id": 42,
            "node_id": "RS_42",
            "source": REPO,
            "created_at": "2026-01-01T00:00:00Z",
            "updated_at": "2026-10-01T00:00:00Z",
            "_links": {"self": {"href": "https://api.github.com/x"}},
            "current_user_can_bypass": "always",
        }
    )
    changes = gs.plan(declared, live_view(rulesets=[live_rs]))
    assert ("same", "ruleset", "main-protection") in triples(changes)
    assert not any(c.op != "same" for c in changes)


# 10. live-only labels, rulesets and variables are reported as extra and never deleted
def test_diff_never_plans_delete_for_live_only_items():
    live = live_view(
        labels=[{"name": "legacy", "color": "ffffff", "description": ""}],
        rulesets=[ruleset("old-ruleset")],
        variables={"OLD_VAR": "x"},
    )
    changes = triples(gs.plan({}, live))
    assert ("extra", "label", "legacy") in changes
    assert ("extra", "ruleset", "old-ruleset") in changes
    assert ("extra", "variable", "OLD_VAR") in changes

    fake = FakeGitHub(
        repo={},
        labels=live["labels"],
        rulesets=live["rulesets"],
        variables=live["variables"],
    )
    gs.run_sync(fake, REPO, {}, apply=True)
    assert not any(method == "DELETE" for method, _, _ in fake.calls)
    assert [label["name"] for label in fake.labels] == ["legacy"]


# 11. apply is idempotent: the second run against the mutated stub writes nothing
def test_apply_is_idempotent():
    declared = {
        "repo": {"allow_squash_merge": True, "delete_branch_on_merge": True},
        "rulesets": [ruleset()],
        "labels": [{"name": "status:ready", "color": "0e8a16", "description": "Ready"}],
        "variables": {"ORG_NAME": "kgsmith19"},
    }
    fake = FakeGitHub(repo={"allow_squash_merge": False, "delete_branch_on_merge": False})

    first = gs.run_sync(fake, REPO, declared, apply=True)
    assert any(c.op != "same" for c in first)
    assert fake.writes(), "first apply must perform writes"

    fake.calls.clear()
    second = gs.run_sync(fake, REPO, declared, apply=True)
    assert all(c.op == "same" for c in second)
    assert fake.writes() == []


# 12. default (no --apply) is a dry run: GETs only, state unchanged
def test_dry_run_default_makes_no_writes(tmp_path):
    settings = tmp_path / "github-settings.json"
    settings.write_text(
        json.dumps(doc_for({"repo": {"allow_squash_merge": True}, "variables": {"ORG_NAME": "kgsmith19"}})),
        encoding="utf-8",
    )
    fake = FakeGitHub(repo={"allow_squash_merge": False})
    before = copy.deepcopy((fake.repo_settings, fake.variables, fake.labels, fake.rulesets))
    out = io.StringIO()

    code = gs.main(["--repo", REPO, "--settings", str(settings)], api=fake, out=out)

    assert code == 0
    assert fake.writes() == []
    assert (fake.repo_settings, fake.variables, fake.labels, fake.rulesets) == before
    assert "dry-run" in out.getvalue()


# 13. output carries names only, never variable values
def test_output_logs_names_not_values(tmp_path):
    settings = tmp_path / "github-settings.json"
    settings.write_text(json.dumps(doc_for({"variables": {"ORG_NAME": "DECL-SENTINEL-4"}})), encoding="utf-8")
    fake = FakeGitHub(variables={"ORG_NAME": "LIVE-SENTINEL-9"})
    out = io.StringIO()

    code = gs.main(["--repo", REPO, "--settings", str(settings), "--apply"], api=fake, out=out)

    text = out.getvalue()
    assert code == 0
    assert "ORG_NAME" in text
    assert "SENTINEL" not in text
    assert fake.variables["ORG_NAME"] == "DECL-SENTINEL-4"


# 14. workflow: dispatch-only, permissions {}, no PAT, pinned actions, fail-closed message
def test_workflow_permissions_fail_closed_and_no_pat():
    text = (REPO_ROOT / ".github/workflows/apply-github-settings.yml").read_text(encoding="utf-8")

    assert re.search(r"^permissions:\s*\{\}\s*$", text, re.M), "top-level permissions must be {}"
    assert re.search(r"^on:\s*\n\s+workflow_dispatch:", text, re.M), "trigger must be workflow_dispatch"
    assert not re.search(r"^\s{2}(push|pull_request|pull_request_target|schedule):", text, re.M)
    assert not re.search(r"secrets\.[A-Za-z0-9_]*PAT", text, re.I)
    assert "id-token: write" in text
    assert "/settings-app/" in text
    assert "SETTINGS_APP_ID" in text and "SETTINGS_APP_PRIVATE_KEY" in text
    assert "settings App not provisioned (see ext#97)" in text
    for uses in re.findall(r"^\s*-?\s*uses:\s*(\S+)", text, re.M):
        assert re.search(r"@[0-9a-f]{40}\b", uses), f"action not pinned to a SHA: {uses}"


def test_workflow_mints_the_app_token_and_passes_it_as_gh_token():
    """The reconcile step gets a settings-App installation token, never a PAT.

    Catches the apply step running with no token (the module then refuses)
    or with a long-lived credential instead of a minted installation token.
    """
    text = (REPO_ROOT / ".github/workflows/apply-github-settings.yml").read_text(encoding="utf-8")
    mint = re.search(
        r"id:\s*app-token\s*\n\s*uses:\s*actions/create-github-app-token@[0-9a-f]{40}", text)
    assert mint, "settings App token must be minted by the pinned create-github-app-token action"
    reconcile = text.split("Reconcile declared settings", 1)[1]
    assert "GH_TOKEN: ${{ steps.app-token.outputs.token }}" in reconcile


def test_undeclared_label_description_is_left_untouched():
    """A label declared without a description never blanks the live one.

    Catches apply PATCHing description="" over an existing description when
    only the color drifted, or reporting drift when only the description is
    set live.
    """
    doc = {"schema_version": 1,
           "repos": {REPO: {"labels": [{"name": "status:ready", "color": "0e8a16"}]}}}
    declared = gs.validate(doc)["repos"][REPO]
    fake = FakeGitHub(labels=[{"name": "status:ready", "color": "0e8a16", "description": "Live text"}])
    assert all(c.op == "same" for c in gs.run_sync(fake, REPO, declared, apply=True))
    assert fake.writes() == []

    fake = FakeGitHub(labels=[{"name": "status:ready", "color": "ffffff", "description": "Live text"}])
    gs.run_sync(fake, REPO, declared, apply=True)
    patches = [p for m, _, p in fake.writes() if m == "PATCH"]
    assert patches == [{"color": "0e8a16"}], patches
