"""Kilo bounded-task canary (#36, R3): verdict logic, scripted model, workflow shape.

The unit tests read recorded JSON event fixtures from tests/fixtures/kilo/ and
need no network and no Kilo CLI. The one integration test runs the full canary
against a real pinned CLI and is skipped unless KILO_BIN names its binary.
"""

import argparse
import copy
import hashlib
import json
import os
import re
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib import request as urlrequest

import pytest

from agent_extensions.sync.harness import ADAPTERS

REPO = Path(__file__).resolve().parents[1]
CANARY = REPO / "canary" / "kilo"
FIXTURES = REPO / "tests" / "fixtures" / "kilo"
WORKFLOW = REPO / ".github" / "workflows" / "kilo-canary.yml"

from canary.kilo import run_canary as rc  # noqa: E402
from canary.kilo import scripted_model as sm  # noqa: E402


def _events(name):
    text = (FIXTURES / name).read_text(encoding="utf-8")
    events, errors = rc.parse_events(text)
    assert errors == [], errors
    return events


# --- scripted model (the one double) -------------------------------------------------


def _serve(cfg):
    server = ThreadingHTTPServer(("127.0.0.1", 0), sm._make_handler(cfg))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, f"http://127.0.0.1:{server.server_address[1]}/v1/chat/completions"


def _post(url, messages):
    body = json.dumps({"model": "m1", "stream": True, "messages": messages,
                       "tools": [{"type": "function", "function": {"name": "write"}}]}).encode()
    req = urlrequest.Request(url, data=body, headers={"Content-Type": "application/json"})
    with urlrequest.urlopen(req, timeout=10) as resp:
        return resp.read().decode()


def _config(**overrides):
    base = dict(script="tool-once", tool_name="write",
                tool_args='{"filePath":"allowed.txt","content":"hello\\n"}',
                chunks=3, interval=0.0, port=0, log="", ready_file="")
    base.update(overrides)
    return sm._Config(argparse.Namespace(**base))


def test_scripted_model_emits_the_configured_tool_call_then_answers():
    server, url = _serve(_config())
    try:
        first = _post(url, [{"role": "user", "content": "write allowed"}])
        second = _post(url, [{"role": "user", "content": "write allowed"},
                             {"role": "assistant", "content": ""},
                             {"role": "tool", "tool_call_id": "call_1", "content": "ok"}])
    finally:
        server.shutdown()
        server.server_close()
    chunks = [json.loads(line[6:]) for line in first.splitlines() if line.startswith("data: {")]
    calls = [c for c in chunks if "tool_calls" in c["choices"][0]["delta"]]
    assert len(calls) == 1
    call = calls[0]["choices"][0]["delta"]["tool_calls"][0]["function"]
    assert call["name"] == "write"
    assert json.loads(call["arguments"]) == {"filePath": "allowed.txt", "content": "hello\n"}
    assert "tool_calls" not in second
    assert '"content": "done"' in second


def test_scripted_stream_slow_streams_chunks_and_never_calls_a_tool(tmp_path):
    log = tmp_path / "requests.jsonl"
    server, url = _serve(_config(script="stream-slow", chunks=4, log=str(log)))
    try:
        text = _post(url, [{"role": "user", "content": "x"}])
    finally:
        server.shutdown()
        server.server_close()
    assert '"tool_calls"' not in text
    lines = [json.loads(x) for x in log.read_text().splitlines()]
    assert sum(1 for x in lines if x["event"] == "chunk") == 4
    assert lines[0]["event"] == "request" and lines[0]["roles"] == ["user"]


# --- allow -----------------------------------------------------------------------------


def test_allow_passes_on_completed_write_with_expected_content():
    verdict = rc.judge_allow(_events("allow_events.jsonl"), {"allowed.txt": "hello\n"})
    assert verdict["verdict"] == "pass", verdict


def test_allow_fails_when_file_content_differs():
    verdict = rc.judge_allow(_events("allow_events.jsonl"), {"allowed.txt": "tampered\n"})
    assert verdict["verdict"] == "fail"
    assert "content" in verdict["reason"]


def test_allow_fails_when_file_is_missing_even_if_tool_completed():
    verdict = rc.judge_allow(_events("allow_events.jsonl"), {})
    assert verdict["verdict"] == "fail"


def test_allow_fails_when_the_tool_call_errored():
    verdict = rc.judge_allow(_events("deny_events.jsonl"), {"allowed.txt": "hello\n"})
    assert verdict["verdict"] == "fail"


# --- deny ------------------------------------------------------------------------------


def test_deny_passes_only_when_rule_is_cited_and_file_absent():
    verdict = rc.judge_deny(_events("deny_events.jsonl"), {}, filename="forbidden.txt",
                            permission="edit")
    assert verdict["verdict"] == "pass", verdict
    assert verdict["evidence"]["rule"] == {"permission": "edit", "pattern": "*", "action": "deny"}


def test_deny_fails_when_the_denial_has_no_rule_citation():
    events = _events("deny_events.jsonl")
    for event in events:
        state = event.get("part", {}).get("state")
        if state and state.get("status") == "error":
            state["error"] = "something went wrong"
            state.pop("metadata", None)
    verdict = rc.judge_deny(events, {}, filename="forbidden.txt", permission="edit")
    assert verdict["verdict"] == "fail"
    assert "wrong reason" in verdict["reason"]


def test_deny_fails_when_the_write_completed_even_if_a_rule_is_cited():
    # A false permit: the tool call completed, so a cited deny rule proves nothing.
    events = _events("deny_events.jsonl")
    for event in events:
        state = event.get("part", {}).get("state")
        if state and state.get("status") == "error":
            state["status"] = "completed"
    verdict = rc.judge_deny(events, {}, filename="forbidden.txt", permission="edit")
    assert verdict["verdict"] == "fail"
    assert "was not denied" in verdict["reason"]


def test_deny_fails_for_missing_setup_even_though_file_is_absent():
    # "tool not offered" is a denial by missing setup, not by rule: wrong reason.
    verdict = rc.judge_deny(_events("bash_unavailable_events.jsonl"), {},
                            filename="gone.txt", permission="bash")
    assert verdict["verdict"] == "fail"
    assert "wrong reason" in verdict["reason"]


def test_deny_fails_when_the_forbidden_file_was_created():
    verdict = rc.judge_deny(_events("deny_events.jsonl"), {"forbidden.txt": "x"},
                            filename="forbidden.txt", permission="edit")
    assert verdict["verdict"] == "fail"


def test_bash_deny_passes_when_bash_is_denied_by_rule():
    verdict = rc.judge_deny(_events("bash_deny_events.jsonl"), {}, filename="gone.txt",
                            permission="bash")
    assert verdict["verdict"] == "pass", verdict


def test_deny_rule_must_be_a_deny_action():
    events = _events("deny_events.jsonl")
    for event in events:
        state = event.get("part", {}).get("state", {})
        if state.get("status") == "error":
            state["metadata"]["approval"]["rule"]["action"] = "allow"
    verdict = rc.judge_deny(events, {}, filename="forbidden.txt", permission="edit")
    assert verdict["verdict"] == "fail"


# --- resume ----------------------------------------------------------------------------


def _resume_args(**overrides):
    run1 = _events("resume_run1_events.jsonl")
    run2 = _events("resume_run2_events.jsonl")
    base = dict(
        run1_events=run1,
        run2_events=run2,
        run1_session=run1[0]["sessionID"],
        run2_session_requested=run1[0]["sessionID"],
        run2_roles=["system", "user", "assistant", "tool", "assistant", "user"],
        run1_server_pid=1001,
        run2_server_pid=1002,
        run2_returncode=0,
        run1_identity={"session_id": run1[0]["sessionID"], "workspace_sha256": "abc"},
        run2_identity={"session_id": run1[0]["sessionID"], "workspace_sha256": "abc"},
    )
    base.update(overrides)
    return base


def test_resume_passes_on_same_session_prior_messages_and_identical_receipt():
    verdict = rc.judge_resume(**_resume_args())
    assert verdict["verdict"] == "pass", verdict


def test_resume_fails_when_the_session_id_differs():
    args = _resume_args()
    other = "ses_otherSessionIdentityXXXXXXXX"
    args["run2_events"] = copy.deepcopy(args["run2_events"])
    for event in args["run2_events"]:
        event["sessionID"] = other
    args["run2_identity"] = {"session_id": other, "workspace_sha256": "abc"}
    verdict = rc.judge_resume(**args)
    assert verdict["verdict"] == "fail"


def test_resume_fails_when_events_carry_another_session_even_if_identity_matches():
    # Survivor fix: the identity claims the original session, so only the event
    # stream's session ids can expose a different session.
    args = _resume_args()
    other = "ses_otherSessionIdentityXXXXXXXX"
    args["run2_events"] = copy.deepcopy(args["run2_events"])
    for event in args["run2_events"]:
        event["sessionID"] = other
    verdict = rc.judge_resume(**args)
    assert verdict["verdict"] == "fail"
    assert "original session id" in verdict["reason"]


def test_resume_fails_when_the_second_run_started_a_new_session():
    args = _resume_args(run2_session_requested="ses_notTheSameSession00000000")
    assert rc.judge_resume(**args)["verdict"] == "fail"


def test_resume_fails_when_prior_messages_are_not_in_the_request():
    args = _resume_args(run2_roles=["system", "user"])
    assert rc.judge_resume(**args)["verdict"] == "fail"


def test_resume_fails_when_the_workspace_state_differs():
    args = _resume_args(run2_identity={"session_id": _resume_args()["run1_session"],
                                       "workspace_sha256": "different"})
    assert rc.judge_resume(**args)["verdict"] == "fail"


def test_resume_fails_when_it_reused_a_warm_model_process():
    assert rc.judge_resume(**_resume_args(run2_server_pid=1001))["verdict"] == "fail"


# --- cancel ----------------------------------------------------------------------------


def test_cancel_without_acknowledgment_is_a_gap_not_a_pass():
    verdict = rc.judge_cancel(_events("cancel_events.jsonl"), terminated=True,
                              returncode=-2, sent_signal="SIGINT", streamed_chunks=6)
    assert verdict["verdict"] == "gap", verdict
    assert verdict["gap_id"] == "cancel-ack"
    assert verdict["evidence"]["acknowledged"] is False
    assert verdict["evidence"]["returncode"] == -2


def test_cancel_with_an_explicit_acknowledgment_passes():
    events = _events("cancel_events.jsonl") + [{
        "type": "step_finish", "sessionID": "ses_x",
        "part": {"type": "step-finish", "reason": "cancelled"}}]
    verdict = rc.judge_cancel(events, terminated=True, returncode=-2,
                              sent_signal="SIGINT", streamed_chunks=6)
    assert verdict["verdict"] == "pass", verdict


def test_cancel_reported_as_success_fails():
    events = _events("cancel_events.jsonl") + [{
        "type": "step_finish", "sessionID": "ses_x",
        "part": {"type": "step-finish", "reason": "stop"}}]
    verdict = rc.judge_cancel(events, terminated=True, returncode=0,
                              sent_signal="SIGINT", streamed_chunks=6)
    assert verdict["verdict"] == "fail"


def test_cancel_still_running_after_kill_fails():
    verdict = rc.judge_cancel(_events("cancel_events.jsonl"), terminated=False,
                              returncode=None, sent_signal="SIGKILL", streamed_chunks=6)
    assert verdict["verdict"] == "fail"


def test_cancel_that_never_streamed_is_not_exercised_and_fails():
    verdict = rc.judge_cancel(_events("cancel_events.jsonl"), terminated=True,
                              returncode=-2, sent_signal="SIGINT", streamed_chunks=0)
    assert verdict["verdict"] == "fail"


# --- launcher-side acknowledged-cancel receipt (#92) ----------------------------------

BEFORE_FIXTURE = "cancel_receipt_before.json"
AFTER_FIXTURE = "cancel_receipt_after.json"


def _fixture_list(name):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def _receipt_inputs(**overrides):
    """Kwargs for rc.cancel_receipt describing a clean SIGINT that emptied the group."""
    base = {"signal_sent": "SIGINT",
            "signals": [{"signal": "SIGINT", "sent_at_ms": 0, "exited_after_ms": 412}],
            "pgid": 12345, "returncode": -2, "terminated": True, "group_empty_after_exit": True,
            "elapsed_ms": 412, "last_event": {"type": "step_start", "sessionID": "ses_x"},
            "streamed_chunks": 6, "client_disconnected": True,
            "before": _fixture_list(BEFORE_FIXTURE), "after": _fixture_list(BEFORE_FIXTURE)}
    base.update(overrides)
    return base


def _launcher_receipt(**overrides):
    return rc.cancel_receipt(**_receipt_inputs(**overrides))


def _judge_with_receipt(receipt, **overrides):
    args = {"terminated": True, "returncode": -2, "sent_signal": "SIGINT", "streamed_chunks": 6,
            "launcher_receipt": receipt}
    args.update(overrides)
    return rc.judge_cancel(_events("cancel_events.jsonl"), **args)


def test_cancel_receipt_is_issued_by_the_launcher_and_acknowledges_the_cancel():
    receipt = _launcher_receipt()
    assert receipt["verdict"] == "cancelled-acknowledged"
    assert receipt["issuer"] == "launcher"
    assert receipt["kilo_cancel_event"] is None
    assert receipt["signal_sent"] == "SIGINT"
    assert receipt["signals"] == [{"signal": "SIGINT", "sent_at_ms": 0, "exited_after_ms": 412}]
    assert receipt["process_group"] == {"pgid": 12345, "returncode": -2, "terminated": True,
                                        "group_empty_after_exit": True}
    assert receipt["elapsed_ms"] == 412
    assert receipt["last_event"] == {"type": "step_start", "sessionID": "ses_x"}
    assert receipt["streamed_chunks"] == 6
    assert receipt["client_disconnected"] is True


def test_cancel_receipt_with_an_unchanged_workspace_lists_no_partial_effects():
    before = _fixture_list(BEFORE_FIXTURE)
    receipt = _launcher_receipt(before=before, after=list(before))
    assert receipt["workspace_unchanged"] is True
    assert receipt["partial_effects"] == []


def test_cancel_receipt_lists_files_changed_after_the_cancel_point_as_partial_effects():
    receipt = _launcher_receipt(before=_fixture_list(BEFORE_FIXTURE),
                                after=_fixture_list(AFTER_FIXTURE))
    assert receipt["workspace_unchanged"] is False
    assert [e["path"] for e in receipt["partial_effects"]] == ["notes.txt", "partial.txt"]


def test_cancel_receipt_lists_a_file_removed_after_the_cancel_point():
    before = _fixture_list(BEFORE_FIXTURE)
    receipt = _launcher_receipt(before=before, after=before[1:])
    assert receipt["workspace_unchanged"] is False
    assert [e["path"] for e in receipt["partial_effects"]] == ["allowed.txt"]


def test_workspace_hashes_are_byte_exact_and_sorted_by_path(tmp_path):
    (tmp_path / "sub").mkdir()
    (tmp_path / "b.txt").write_bytes(b"hello\n")
    (tmp_path / "sub" / "a.bin").write_bytes(b"\xff\xfe\x00")
    assert rc.workspace_hashes(tmp_path) == [
        {"path": "b.txt", "sha256": hashlib.sha256(b"hello\n").hexdigest()},
        {"path": "sub/a.bin", "sha256": hashlib.sha256(b"\xff\xfe\x00").hexdigest()}]


def test_cancel_with_a_consistent_launcher_receipt_passes_on_the_launcher():
    verdict = _judge_with_receipt(_launcher_receipt())
    assert verdict["verdict"] == "pass", verdict
    assert verdict["evidence"]["acknowledged_by"] == "launcher"


def test_cancel_with_an_explicit_none_receipt_is_still_a_gap():
    verdict = rc.judge_cancel(_events("cancel_events.jsonl"), terminated=True, returncode=-2,
                              sent_signal="SIGINT", streamed_chunks=6, launcher_receipt=None)
    assert verdict["verdict"] == "gap"
    assert verdict["gap_id"] == "cancel-ack"


def test_kilo_acknowledgment_still_wins_over_the_launcher_receipt():
    events = _events("cancel_events.jsonl") + [{
        "type": "step_finish", "sessionID": "ses_x",
        "part": {"type": "step-finish", "reason": "cancelled"}}]
    verdict = rc.judge_cancel(events, terminated=True, returncode=-2, sent_signal="SIGINT",
                              streamed_chunks=6, launcher_receipt=_launcher_receipt())
    assert verdict["verdict"] == "pass"
    assert verdict["evidence"]["acknowledged_by"] == "kilo"


def test_receipt_that_says_the_process_still_runs_fails_the_cancel():
    receipt = _launcher_receipt(terminated=False)
    assert _judge_with_receipt(receipt)["verdict"] == "fail"


def test_receipt_for_a_different_signal_than_sent_fails_the_cancel():
    receipt = _launcher_receipt(signal_sent="SIGTERM")
    assert _judge_with_receipt(receipt, sent_signal="SIGINT")["verdict"] == "fail"


def test_receipt_whose_process_group_survived_the_exit_fails_the_cancel():
    receipt = _launcher_receipt(group_empty_after_exit=False)
    assert _judge_with_receipt(receipt)["verdict"] == "fail"


def test_receipt_reporting_a_successful_stop_fails_the_cancel():
    events = _events("cancel_events.jsonl") + [{
        "type": "step_finish", "sessionID": "ses_x",
        "part": {"type": "step-finish", "reason": "stop"}}]
    receipt = _launcher_receipt(returncode=0)
    verdict = rc.judge_cancel(events, terminated=True, returncode=0, sent_signal="SIGINT",
                              streamed_chunks=6, launcher_receipt=receipt)
    assert verdict["verdict"] == "fail"


def test_launcher_receipt_keeps_cancel_ack_declared_as_a_gap_in_the_receipt():
    receipt = _receipt(cancel=_judge_with_receipt(_launcher_receipt()))
    assert receipt["scenarios"]["cancel"]["verdict"] == "pass"
    assert "cancel-ack" in {gap["id"] for gap in receipt["gaps"]}
    assert rc.validate_receipt(receipt) == []
    assert rc.receipt_verdict(receipt) == "gap"


def test_run_scenarios_stores_the_launcher_receipt_as_cancel_evidence(tmp_path):
    receipt = _launcher_receipt()

    class FakeRunner:
        def run_turn(self, name, tag, **_kwargs):
            return {"events": [], "returncode": 0, "workspace": str(tmp_path),
                    "session_id": None, "requests": [], "server_pid": None}

        def run_cancel(self, name, *, message):
            return {"events": _events("cancel_events.jsonl"), "terminated": True,
                    "returncode": -2, "sent_signal": "SIGINT", "streamed_chunks": 6,
                    "client_disconnected": True, "workspace": str(tmp_path),
                    "launcher_receipt": receipt}

    results, _cancel = rc.run_scenarios(FakeRunner())
    assert results["cancel"]["verdict"] == "pass", results["cancel"]
    assert results["cancel"]["evidence"]["launcher_receipt"] == receipt


# --- provenance, receipt shape, gaps ---------------------------------------------------


def _receipt(cancel=None, **overrides):
    scenarios = {
        "allow": rc.judge_allow(_events("allow_events.jsonl"), {"allowed.txt": "hello\n"}),
        "deny": rc.judge_deny(_events("deny_events.jsonl"), {}, filename="forbidden.txt",
                              permission="edit"),
        "resume": rc.judge_resume(**_resume_args()),
        "cancel": cancel or rc.judge_cancel(_events("cancel_events.jsonl"), terminated=True,
                                            returncode=-2, sent_signal="SIGINT",
                                            streamed_chunks=6),
    }
    args = dict(kilo_version="7.8.10", parser_entry={"package": "@kilocode/cli@7.8.10"},
                launcher_entry={"binary": "/x/bin/kilo"}, node_version="v22.19.0",
                provenance=rc.provenance(), scenarios=scenarios, parse_errors=[],
                generated_at="2026-10-27T00:00:00Z")
    args.update(overrides)
    return rc.build_receipt(**args)


def test_scripted_provenance_names_the_deterministic_lane_model():
    prov = rc.provenance()
    assert prov["model"] == "scripted-local/m1 (deterministic lane)"
    assert prov["provider_kind"] == "scripted-openai-compatible-local"
    assert prov["role"] == "implementer"


def test_missing_provenance_fails_the_receipt():
    receipt = _receipt(provenance=None)
    assert rc.receipt_verdict(receipt) == "fail"
    assert any("provenance" in err for err in rc.validate_receipt(receipt))


def test_blank_provenance_field_fails_the_receipt():
    receipt = _receipt(provenance=dict(rc.provenance(), model=""))
    assert rc.receipt_verdict(receipt) == "fail"


def test_parse_errors_fail_the_receipt():
    assert rc.receipt_verdict(_receipt(parse_errors=["line 3: not JSON"])) == "fail"


def test_receipt_lists_every_kilo_adapter_gap():
    receipt = _receipt()
    listed = {gap["id"] for gap in receipt["gaps"]}
    for gap in ADAPTERS["kilo"].gaps():
        assert gap in listed, gap
    assert "cancel-ack" in listed


def test_receipt_verdict_is_gap_not_pass_when_only_gaps_remain():
    receipt = _receipt()
    assert receipt["scenarios"]["cancel"]["verdict"] == "gap"
    assert rc.receipt_verdict(receipt) == "gap"


def test_receipt_verdict_is_fail_when_any_scenario_fails():
    receipt = _receipt()
    receipt["scenarios"]["allow"]["verdict"] = "fail"
    assert rc.receipt_verdict(receipt) == "fail"


def test_receipt_schema_fields_are_present():
    receipt = _receipt()
    for key in ("canary", "kilo_version", "parser_entry", "launcher_entry", "node_version",
                "lane", "provenance", "scenarios", "gaps", "generated_at"):
        assert key in receipt, key
    assert set(receipt["scenarios"]) == {"allow", "deny", "resume", "cancel"}
    for name, scenario in receipt["scenarios"].items():
        assert scenario["verdict"] in {"pass", "fail", "gap"}, name
        assert "evidence" in scenario, name


# --- fixtures, template, workflow ------------------------------------------------------


def test_parse_events_reports_non_json_lines_instead_of_dropping_them():
    events, errors = rc.parse_events('{"type":"step_start"}\nnot json\n')
    assert len(events) == 1
    assert len(errors) == 1 and "line 2" in errors[0]


def test_fixtures_contain_no_absolute_host_paths():
    for path in FIXTURES.glob("*.jsonl"):
        text = path.read_text(encoding="utf-8")
        assert not re.search(r"(^|[\"\s])/(tmp|home|root|Users)/", text), path.name


def test_kilo_template_denies_by_default_and_allows_only_named_rules():
    template = (CANARY / "kilo.json.template").read_text(encoding="utf-8")
    rendered = rc.render_config(template, provider={"scripted": {"npm": "x"}},
                                model="scripted/m1")
    assert rendered["permission"]["*"] == "deny"
    assert rendered["permission"]["read"] == "allow"
    assert rendered["permission"]["edit"]["**/allowed.txt"] == "allow"
    assert rendered["permission"]["edit"]["*"] == "deny"
    assert rendered["permission"]["bash"]["*"] == "deny"


def test_workflow_pins_actions_by_full_sha_and_keeps_live_lane_dispatch_only():
    text = WORKFLOW.read_text(encoding="utf-8")
    for uses in re.findall(r"uses:\s*(\S+)", text):
        assert re.search(r"@[0-9a-f]{40}\b", uses), uses
    assert "contents: read" in text
    assert "Kilo canary · deterministic lane" in text
    assert "Kilo canary · live lane" in text
    assert "77ab1f4ccd183a543cb5b42435fbd181189f4995" in text
    head, jobs = text.split("jobs:", 1)
    assert "pull_request:" in head and "workflow_dispatch:" in head
    live = jobs.split("  live:", 1)[1]
    assert "if: github.event_name == 'workflow_dispatch' && inputs.lane == 'live'" in live
    assert "id-token: write" in live
    deterministic = jobs.split("  live:", 1)[0]
    assert "id-token" not in deterministic
    assert "Infisical" not in deterministic


def test_live_lane_config_is_non_secret_and_not_provisioned_by_default():
    cfg = json.loads((CANARY / "live-lane.json").read_text(encoding="utf-8"))
    assert cfg["infisical_identity_id"] == ""
    assert cfg["secret_name"] == "DEV_KILO_API_KEY"
    assert cfg["secret_path"] == "/dev/"
    assert cfg["model"].startswith("kilo/")


# --- integration: real pinned CLI (only when KILO_BIN is set) -------------------------


@pytest.mark.skipif(not os.environ.get("KILO_BIN"), reason="set KILO_BIN to a pinned kilo binary")
def test_full_canary_against_the_real_cli(tmp_path):
    out = tmp_path / "receipt.json"
    code = rc.main(["--kilo-bin", os.environ["KILO_BIN"], "--out", str(out),
                    "--work", str(tmp_path / "work")])
    assert code == 0
    receipt = json.loads(out.read_text(encoding="utf-8"))
    assert receipt["kilo_version"] == "7.8.10"
    assert receipt["scenarios"]["allow"]["verdict"] == "pass"
    assert receipt["scenarios"]["deny"]["verdict"] == "pass"
    assert receipt["scenarios"]["resume"]["verdict"] == "pass"
    cancel = receipt["scenarios"]["cancel"]
    assert cancel["verdict"] == "pass"
    assert cancel["evidence"]["launcher_receipt"]["issuer"] == "launcher"
    assert "cancel-ack" in {gap["id"] for gap in receipt["gaps"]}
    assert rc.receipt_verdict(receipt) == "gap"


def test_live_lane_reads_infisical_like_the_other_agent_workflows():
    """DEV_* agent keys live in Infisical env prod under /dev/ (as hyp's dev agent reads them);
    a dev env-slug would fetch nothing and the live lane would run keyless."""
    live = (REPO / ".github" / "workflows" / "kilo-canary.yml").read_text(encoding="utf-8").split("  live:", 1)[1]
    assert "env-slug: prod" in live
    assert "domain: https://app.infisical.com" in live
