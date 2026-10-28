"""Kilo bounded-task canary (#36, R3): real Kilo parser and launcher, scripted model.

Runs the pinned Kilo CLI (kilo run --format json) against a deterministic
OpenAI-compatible model server (scripted_model.py) and writes receipt.json.
The model server is the only double. The event parser (the JSON stream read
here), the permission engine and the launcher are the real CLI.

    python3 canary/kilo/run_canary.py --kilo-bin /path/to/node_modules/.bin/kilo \\
        --out receipt.json

Exit status: 0 when no scenario fails (gaps are listed, not failures),
1 when any scenario fails or the receipt is malformed, 2 on setup errors.
The install of the CLI is not done here; see canary/kilo/README.md.
"""

import argparse
import datetime
import glob
import hashlib
import json
import os
import re
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
if str(REPO) not in sys.path:  # run as a script: make agent_extensions importable
    sys.path.insert(0, str(REPO))

from agent_extensions.sync.harness import ADAPTERS  # noqa: E402

CANARY = "kilo-bounded-task"
KILO_VERSION = "7.8.10"
KILO_PACKAGE = "@kilocode/cli@7.8.10"
DETERMINISTIC_MODEL = "scripted-local/m1 (deterministic lane)"
PROVIDER_KIND = "scripted-openai-compatible-local"
SCRIPTED_MODEL_ID = "scripted/m1"
TEMPLATE = HERE / "kilo.json.template"
SCRIPTED_MODEL = HERE / "scripted_model.py"
LIVE_CONFIG = HERE / "live-lane.json"

ALLOWED = "allowed.txt"
ALLOWED_CONTENT = "hello\n"
FORBIDDEN = "forbidden.txt"
BASH_FILE = "gone.txt"
BASH_COMMAND = "printf hi > gone.txt"
WRITE_TOOLS = ("write", "edit")
ACK_REASONS = ("cancelled", "canceled", "aborted", "interrupted")
SCENARIOS = ("allow", "deny", "resume", "cancel")
VERDICTS = ("pass", "fail", "gap")
RULE_RE = re.compile(r'\{"permission":[^{}]*\}')
PROVIDER_BLOCK = {
    "scripted": {
        "npm": "@ai-sdk/openai-compatible",
        "name": "Scripted",
        "options": {"baseURL": "http://127.0.0.1:{port}/v1", "apiKey": "none"},
        "models": {"m1": {"name": "m1"}},
    }
}


# --- pure logic: events, verdicts, receipt --------------------------------------------


def parse_events(text):
    """Parse a `kilo run --format json` stream. Returns (events, errors).

    A line that is not a JSON object is an error, never silently dropped.
    """
    events, errors = [], []
    for number, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except ValueError:
            errors.append(f"line {number}: not JSON")
            continue
        if not isinstance(value, dict):
            errors.append(f"line {number}: not a JSON object")
            continue
        events.append(value)
    return events, errors


def provenance(lane="deterministic", model=None):
    """Who did the work: the Kilo agent, the model, and the one double."""
    if lane == "live":
        model_label = model or ""
        provider_kind = "kilo-provider-live"
        double = "none"
    else:
        model_label = DETERMINISTIC_MODEL
        provider_kind = PROVIDER_KIND
        double = "scripted-model-server (stands in for the LLM provider only)"
    return {
        "role": "implementer",
        "agent": "kilo default agent (no --agent override)",
        "model": model_label,
        "provider_kind": provider_kind,
        "double": double,
    }


def _state(event):
    part = event.get("part") or {}
    return part.get("state") or {}


def _tool_calls(events):
    return [e for e in events if e.get("type") == "tool_use"]


def _call_path(event):
    return ((_state(event).get("input")) or {}).get("filePath")


def _fail(reason, **evidence):
    return {"verdict": "fail", "reason": reason, "evidence": evidence}


def _pass(reason, **evidence):
    return {"verdict": "pass", "reason": reason, "evidence": evidence}


def judge_allow(events, files, *, filename=ALLOWED, expected=ALLOWED_CONTENT):
    """Allowed in-scope edit: completed write, and the file holds the expected bytes."""
    calls = [e for e in _tool_calls(events)
             if e["part"].get("tool") in WRITE_TOOLS and _call_path(e) == filename]
    if not calls:
        return _fail(f"no write to {filename} reached Kilo tool dispatch")
    call = calls[-1]
    state = _state(call)
    evidence = {"session_id": call.get("sessionID"), "tool": call["part"].get("tool"),
                "status": state.get("status")}
    if state.get("status") != "completed":
        return _fail(f"write to {filename} did not complete (status {state.get('status')!r})",
                     **evidence)
    if filename not in files:
        return _fail(f"write reported completed but {filename} is absent", **evidence)
    evidence["file_sha256"] = hashlib.sha256(files[filename].encode("utf-8")).hexdigest()
    if files[filename] != expected:
        return _fail(f"{filename} content differs from the expected content", **evidence)
    return _pass(f"{filename} written by an allowed edit with the expected content", **evidence)


def _cited_rule(state):
    rule = ((state.get("metadata") or {}).get("approval") or {}).get("rule")
    if isinstance(rule, dict):
        return rule
    match = RULE_RE.search(state.get("error") or "")
    if match:
        try:
            parsed = json.loads(match.group(0))
        except ValueError:
            return None
        return parsed if isinstance(parsed, dict) else None
    return None


def judge_deny(events, files, *, filename, permission):
    """Forbidden action denied BY RULE: an error that cites a deny rule, and no effect."""
    if permission == "bash":
        calls = [e for e in _tool_calls(events) if e["part"].get("tool") == "bash"]
        target = "bash command"
    else:
        calls = [e for e in _tool_calls(events)
                 if e["part"].get("tool") in WRITE_TOOLS and _call_path(e) == filename]
        target = f"write to {filename}"
    if not calls:
        return _fail(f"no {target} reached Kilo tool dispatch")
    call = calls[-1]
    state = _state(call)
    evidence = {"session_id": call.get("sessionID"), "tool": call["part"].get("tool"),
                "status": state.get("status"), "error": (state.get("error") or "")[:300]}
    if state.get("status") != "error":
        return _fail(f"{target} was not denied (status {state.get('status')!r})", **evidence)
    rule = _cited_rule(state)
    if rule is None:
        return _fail(f"denied for the wrong reason: no permission rule cited for the {target}",
                     **evidence)
    evidence["rule"] = {k: rule.get(k) for k in ("permission", "pattern", "action")}
    if rule.get("action") != "deny" or rule.get("permission") != permission:
        return _fail(f"cited rule is not a deny rule for {permission}", **evidence)
    evidence["file_absent"] = filename not in files
    if filename in files:
        return _fail(f"{filename} exists: the denial did not stop the action", **evidence)
    return _pass(f"{target} denied by rule {rule.get('permission')}:{rule.get('pattern')}"
                 f" ({rule.get('action')})", **evidence)


def _session_ids(events):
    return {e.get("sessionID") for e in events if e.get("sessionID")}


def judge_resume(*, run1_events, run2_events, run1_session, run2_session_requested,
                 run2_roles, run1_server_pid, run2_server_pid, run2_returncode,
                 run1_identity, run2_identity):
    """Fresh process resumes the same session: same id, prior messages sent, same state."""
    evidence = {"session_id": run1_session, "run1_server_pid": run1_server_pid,
                "run2_server_pid": run2_server_pid, "run2_roles": list(run2_roles),
                "run1_identity": run1_identity, "run2_identity": run2_identity}
    if not run1_session:
        return _fail("first run produced no session id", **evidence)
    if _session_ids(run1_events) != {run1_session}:
        return _fail("first run events do not carry one session id", **evidence)
    if not run2_events or _session_ids(run2_events) != {run1_session}:
        return _fail("resumed run events do not carry the original session id", **evidence)
    if run2_session_requested != run1_session:
        return _fail("second run was not started with the original session id", **evidence)
    if "tool" not in run2_roles or list(run2_roles).count("user") < 2:
        return _fail("prior messages were not present in the request the model received",
                     **evidence)
    if run2_server_pid == run1_server_pid:
        return _fail("second run reused the first model process (not a cold resume)", **evidence)
    if run2_returncode != 0:
        return _fail(f"resumed run exited {run2_returncode}", **evidence)
    if run1_identity != run2_identity or run2_identity.get("session_id") != run1_session:
        return _fail("resumed receipt differs: session or final workspace state changed",
                     **evidence)
    return _pass("cold resume continued the same session with identical final state", **evidence)


def workspace_hashes(workspace):
    """Byte-exact sha256 of every regular file under the workspace, sorted by path."""
    hashes = []
    for root, _dirs, files in os.walk(workspace):
        for name in files:
            full = os.path.join(root, name)
            rel = os.path.relpath(full, workspace).replace(os.sep, "/")
            with open(full, "rb") as fh:
                hashes.append({"path": rel, "sha256": hashlib.sha256(fh.read()).hexdigest()})
    return sorted(hashes, key=lambda entry: entry["path"])


def _elapsed_ms(started):
    return int((time.monotonic() - started) * 1000)


def _group_empty(pgid):
    """True once no process remains in the process group (signal 0 probes it)."""
    try:
        os.killpg(pgid, 0)
    except ProcessLookupError:
        return True
    return False


def _partial_effects(before, after):
    """Paths whose content differs between the two snapshots, including adds and removals."""
    old = {entry["path"]: entry["sha256"] for entry in before}
    new = {entry["path"]: entry["sha256"] for entry in after}
    return [{"path": path, "before": old.get(path), "after": new.get(path)}
            for path in sorted(set(old) | set(new)) if old.get(path) != new.get(path)]


def cancel_receipt(*, signal_sent, signals, pgid, returncode, terminated, group_empty_after_exit,
                   elapsed_ms, last_event, streamed_chunks, client_disconnected, before, after):
    """The launcher's own record of a cancel it sent. Pure: shapes data, does no I/O."""
    partial = _partial_effects(before, after)
    return {
        "verdict": "cancelled-acknowledged",
        "issuer": "launcher",
        "issuer_component": "canary/kilo/run_canary.py:Runner.run_cancel",
        "signal_sent": signal_sent,
        "signals": signals,
        "process_group": {"pgid": pgid, "returncode": returncode, "terminated": terminated,
                          "group_empty_after_exit": group_empty_after_exit},
        "elapsed_ms": elapsed_ms,
        "last_event": last_event,
        "streamed_chunks": streamed_chunks,
        "client_disconnected": client_disconnected,
        "workspace_before": before,
        "workspace_after": after,
        "workspace_unchanged": not partial,
        "partial_effects": partial,
        "kilo_cancel_event": None,
    }


def _receipt_problems(receipt, *, terminated, returncode, sent_signal):
    """Why a launcher receipt cannot back a pass. Empty when it is consistent."""
    group = receipt.get("process_group") or {}
    problems = []
    if receipt.get("issuer") != "launcher" or receipt.get("verdict") != "cancelled-acknowledged":
        problems.append("not a launcher-issued cancelled-acknowledged receipt")
    if not terminated or group.get("terminated") is not True:
        problems.append("the process did not terminate")
    if group.get("group_empty_after_exit") is not True:
        problems.append("the process group was not empty after exit")
    if receipt.get("signal_sent") != sent_signal:
        problems.append(f"receipt signal {receipt.get('signal_sent')} != sent {sent_signal}")
    if group.get("returncode") != returncode:
        problems.append(f"receipt returncode {group.get('returncode')} != {returncode}")
    if receipt.get("workspace_unchanged") != (not receipt.get("partial_effects")):
        problems.append("workspace_unchanged disagrees with partial_effects")
    return problems


def judge_cancel(events, *, terminated, returncode, sent_signal, streamed_chunks,
                 launcher_receipt=None):
    """Mid-stream cancel. Kilo's own acknowledgment passes first. Otherwise the launcher's
    consistent receipt passes. With neither, the cancel is a gap."""
    types = [e.get("type") for e in events]
    evidence = {"terminated": terminated, "returncode": returncode, "sent_signal": sent_signal,
                "streamed_chunks": streamed_chunks, "event_types": types,
                "acknowledged": False}
    if not terminated:
        return _fail("process still running after SIGKILL", **evidence)
    if streamed_chunks < 1:
        return _fail("cancel not exercised: the model stream never went live", **evidence)
    finishes = [(e.get("part") or {}).get("reason") for e in events
                if e.get("type") == "step_finish"]
    if returncode == 0 and "stop" in finishes:
        return _fail("cancelled run reported as a successful completion", **evidence)
    ack = [e for e in events if e.get("type") == "step_finish"
           and (e.get("part") or {}).get("reason") in ACK_REASONS]
    if ack:
        evidence["acknowledged"] = True
        evidence["acknowledged_by"] = "kilo"
        evidence["ack_event"] = ack[-1]
        return _pass("cancellation acknowledged by Kilo", **evidence)
    if launcher_receipt is not None:
        problems = _receipt_problems(launcher_receipt, terminated=terminated,
                                     returncode=returncode, sent_signal=sent_signal)
        if problems:
            return _fail("launcher receipt is inconsistent: " + "; ".join(problems), **evidence)
        evidence["acknowledged"] = True
        evidence["acknowledged_by"] = "launcher"
        return _pass("cancellation acknowledged by the launcher receipt; Kilo writes no"
                     " cancel event", **evidence)
    return {"verdict": "gap", "gap_id": "cancel-ack",
            "reason": ("Kilo writes no cancellation acknowledgment; the run was observed"
                       f" terminated (returncode {returncode}, {sent_signal}) and is recorded"
                       " as cancel-observed-terminated, not acknowledged"),
            "evidence": evidence}


def provenance_problems(prov):
    if not isinstance(prov, dict):
        return ["provenance missing"]
    problems = []
    for key in ("role", "agent", "model", "provider_kind"):
        if not isinstance(prov.get(key), str) or not prov.get(key).strip():
            problems.append(f"provenance.{key} missing or blank")
    return problems


def build_receipt(*, kilo_version, parser_entry, launcher_entry, node_version, provenance,
                  scenarios, parse_errors, generated_at, lane="deterministic"):
    gaps = [{"id": gap, "source": "agent_extensions.sync.harness.KiloAdapter",
             "reason": ADAPTERS["kilo"].gap_reason} for gap in ADAPTERS["kilo"].gaps()]
    for name in SCENARIOS:
        scenario = scenarios.get(name) or {}
        if scenario.get("verdict") == "gap":
            gaps.append({"id": scenario["gap_id"], "scenario": name,
                         "reason": scenario["reason"]})
    cancel = scenarios.get("cancel") or {}
    if (cancel.get("verdict") == "pass"
            and (cancel.get("evidence") or {}).get("acknowledged_by") == "launcher"):
        gaps.append({"id": "cancel-ack", "scenario": "cancel",
                     "reason": "Kilo 7.8.10 writes no cancel event; the launcher receipt"
                               " acknowledges the cancel instead"})
    return {
        "canary": CANARY,
        "kilo_version": kilo_version,
        "parser_entry": parser_entry,
        "launcher_entry": launcher_entry,
        "node_version": node_version,
        "lane": lane,
        "provenance": provenance,
        "scenarios": scenarios,
        "gaps": gaps,
        "parse_errors": list(parse_errors),
        "generated_at": generated_at,
    }


def validate_receipt(receipt):
    errors = []
    for key in ("canary", "kilo_version", "parser_entry", "launcher_entry", "node_version",
                "lane", "provenance", "scenarios", "gaps", "generated_at"):
        if key not in receipt:
            errors.append(f"{key} missing")
    errors.extend(provenance_problems(receipt.get("provenance")))
    scenarios = receipt.get("scenarios") or {}
    gap_ids = {g.get("id") for g in receipt.get("gaps") or []}
    for name in SCENARIOS:
        scenario = scenarios.get(name)
        if not isinstance(scenario, dict):
            errors.append(f"scenario {name} missing")
            continue
        if scenario.get("verdict") not in VERDICTS:
            errors.append(f"scenario {name} has no valid verdict")
        if scenario.get("verdict") == "gap" and scenario.get("gap_id") not in gap_ids:
            errors.append(f"scenario {name} is a gap but is not listed in gaps")
    if receipt.get("parse_errors"):
        errors.append("Kilo event stream had lines that are not JSON")
    return errors


def receipt_verdict(receipt):
    """fail on any failed scenario or malformed receipt; gap when only gaps remain."""
    if validate_receipt(receipt):
        return "fail"
    verdicts = [(receipt["scenarios"][name] or {}).get("verdict") for name in SCENARIOS]
    if "fail" in verdicts:
        return "fail"
    if receipt.get("gaps"):
        return "gap"
    return "pass"


def render_config(template_text, *, provider, model):
    """Fill kilo.json.template. Provider is a JSON object, model a model id."""
    text = template_text.replace("{{PROVIDER}}", json.dumps(provider, sort_keys=True))
    text = text.replace("{{MODEL}}", json.dumps(model)[1:-1])
    return json.loads(text)


def scrub(text, replacements):
    for old, new in replacements:
        text = text.replace(old, new)
    return text


# --- host side: entries, processes, workspace -----------------------------------------


def _run_text(argv, env=None, timeout=60):
    proc = subprocess.run(argv, capture_output=True, text=True, env=env, timeout=timeout,
                          stdin=subprocess.DEVNULL)
    return proc.returncode, proc.stdout, proc.stderr


def kilo_entries(kilo_bin, env):
    """Parser and launcher entry points: the binary, its version output, and its packages."""
    rc_, out, err = _run_text([kilo_bin, "--version"], env=env)
    version = out.strip().splitlines()[-1].strip() if out.strip() else ""
    if rc_ != 0 or version != KILO_VERSION:
        raise SystemExit(f"kilo --version gave {version!r} (rc {rc_}); pinned {KILO_VERSION}")
    real = os.path.realpath(kilo_bin)
    cli_dir = os.path.dirname(os.path.dirname(real))  # .../@kilocode/cli
    with open(os.path.join(cli_dir, "package.json"), encoding="utf-8") as fh:
        cli_pkg = json.load(fh)
    # Every installed platform binary package. The launcher picks one at run time
    # (baseline or not, by CPU), so all installed candidates are recorded.
    platforms = []
    for candidate in sorted(glob.glob(os.path.join(os.path.dirname(cli_dir), "cli-linux-x64*"))):
        pkg_path = os.path.join(candidate, "package.json")
        if os.path.exists(pkg_path):
            with open(pkg_path, encoding="utf-8") as fh:
                meta = json.load(fh)
            platforms.append({"name": meta.get("name"), "version": meta.get("version")})
    parser = {"kind": "kilo run --format json (JSON event stream)", "package": KILO_PACKAGE,
              "binary": real, "version_output": version,
              "platform_packages": platforms}
    launcher = {"kind": "kilo launcher (npm bin, selects the platform binary at run time)",
                "package": f"{cli_pkg.get('name')}@{cli_pkg.get('version')}",
                "bin": kilo_bin, "realpath": real,
                "platform_packages": [f"{p['name']}@{p['version']}" for p in platforms]}
    return version, parser, launcher


def node_version(env):
    _, out, _ = _run_text(["node", "--version"], env=env)
    return out.strip() or None


def scenario_env(home, node_dir=None, extra=None):
    """A clean environment: isolated HOME and XDG dirs, no inherited Kilo or provider keys."""
    path = os.environ.get("PATH", "")
    if node_dir:
        path = f"{node_dir}{os.pathsep}{path}"
    env = {"PATH": path, "HOME": home, "LANG": "C.UTF-8", "TMPDIR": home,
           "XDG_CONFIG_HOME": os.path.join(home, ".config"),
           "XDG_DATA_HOME": os.path.join(home, ".local", "share"),
           "XDG_STATE_HOME": os.path.join(home, ".local", "state"),
           "XDG_CACHE_HOME": os.path.join(home, ".cache"), "NO_COLOR": "1"}
    env.update(extra or {})
    return env


def workspace_state(workspace):
    """Every regular file under the workspace, as text, keyed by relative path."""
    state = {}
    for root, _dirs, files in os.walk(workspace):
        for name in files:
            full = os.path.join(root, name)
            rel = os.path.relpath(full, workspace).replace(os.sep, "/")
            with open(full, encoding="utf-8", errors="replace") as fh:
                state[rel] = fh.read()
    return state


def identity(session_id, state):
    digest = hashlib.sha256(json.dumps(state, sort_keys=True).encode("utf-8")).hexdigest()
    return {"session_id": session_id, "workspace_sha256": digest}


def _wait_for_file(path, timeout):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if os.path.exists(path) and os.path.getsize(path) > 0:
            return True
        time.sleep(0.05)
    return False


def start_model(work, tag, script, *, tool=None, chunks=400, interval=0.25):
    """Start the scripted model server for one run. Returns (proc, port, log)."""
    ready = os.path.join(work, f"{tag}.ready")
    log = os.path.join(work, f"{tag}.model.jsonl")
    argv = [sys.executable, "-I", str(SCRIPTED_MODEL), "--script", script, "--ready-file", ready,
            "--log", log, "--chunks", str(chunks), "--interval", str(interval)]
    if tool:
        argv += ["--tool-name", tool["name"], "--tool-args", json.dumps(tool["arguments"])]
    proc = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL)
    if not _wait_for_file(ready, 15):
        proc.kill()
        raise SystemExit(f"scripted model server for {tag} did not start")
    with open(ready, encoding="utf-8") as fh:
        port = int(fh.read().strip())
    return proc, port, log


def stop_model(proc):
    if proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()


def read_model_log(path):
    if not path or not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


def write_config(home, port, model_id, lane, template_text, live_model=None):
    if lane == "live":
        provider = {}
        model = live_model
    else:
        provider = json.loads(json.dumps(PROVIDER_BLOCK).replace("{port}", str(port)))
        model = SCRIPTED_MODEL_ID
    config_dir = os.path.join(home, ".config", "kilo")
    os.makedirs(config_dir, exist_ok=True)
    rendered = render_config(template_text, provider=provider, model=model)
    with open(os.path.join(config_dir, "kilo.json"), "w", encoding="utf-8") as fh:
        json.dump(rendered, fh, indent=2)
    return rendered


class Runner:
    """Runs kilo turns for one scenario in its own HOME and workspace."""

    def __init__(self, kilo_bin, work, *, node_dir, lane, live_model, timeout):
        self.kilo_bin = kilo_bin
        self.work = work
        self.node_dir = node_dir
        self.lane = lane
        self.live_model = live_model
        self.timeout = timeout
        self.template = TEMPLATE.read_text(encoding="utf-8")
        self.parse_errors = []
        self.extra_env = {}
        if lane == "live":
            key = os.environ.get("DEV_KILO_API_KEY", "")
            self.extra_env["KILO_API_KEY"] = key

    def scenario_dirs(self, name):
        base = os.path.join(self.work, name)
        home = os.path.join(base, "home")
        workspace = os.path.join(base, "workspace")
        os.makedirs(home, exist_ok=True)
        os.makedirs(workspace, exist_ok=True)
        return home, workspace

    def _env(self, home):
        return scenario_env(home, self.node_dir, self.extra_env)

    def run_turn(self, name, tag, *, message, script="tool-once", tool=None, session=None):
        """One kilo run against a freshly started model server (a cold process)."""
        home, workspace = self.scenario_dirs(name)
        proc, port, model_log = (None, None, None)
        if self.lane != "live":
            proc, port, model_log = start_model(self.work, f"{name}-{tag}", script, tool=tool)
        try:
            write_config(home, port, SCRIPTED_MODEL_ID, self.lane, self.template,
                         live_model=self.live_model)
            argv = [self.kilo_bin, "run", "--format", "json", "--auto"]
            argv += ["-m", SCRIPTED_MODEL_ID if self.lane != "live" else self.live_model]
            if session:
                argv += ["--session", session]
            argv.append(message)
            stdout_path = os.path.join(self.work, f"{name}-{tag}.stdout.jsonl")
            stderr_path = os.path.join(self.work, f"{name}-{tag}.stderr.txt")
            with open(stdout_path, "w", encoding="utf-8") as out, \
                    open(stderr_path, "w", encoding="utf-8") as err:
                finished = subprocess.run(argv, cwd=workspace, env=self._env(home),
                                          stdin=subprocess.DEVNULL, stdout=out, stderr=err,
                                          timeout=self.timeout)
            with open(stdout_path, encoding="utf-8") as fh:
                events, errors = parse_events(fh.read())
            self.parse_errors.extend(f"{name}-{tag} {err}" for err in errors)
            requests = read_model_log(model_log) if model_log else []
        finally:
            if proc is not None:
                stop_model(proc)
        return {"events": events, "returncode": finished.returncode, "home": home,
                "workspace": workspace, "requests": requests,
                "server_pid": proc.pid if proc else None,
                "session_id": next((e["sessionID"] for e in events if e.get("sessionID")), None),
                "stdout_path": stdout_path, "stderr_path": stderr_path}

    def run_cancel(self, name, *, message):
        home, workspace = self.scenario_dirs(name)
        proc, port, model_log = (None, None, None)
        if self.lane != "live":
            proc, port, model_log = start_model(self.work, f"{name}-cancel", "stream-slow",
                                                chunks=400, interval=0.25)
        stdout_path = os.path.join(self.work, f"{name}-cancel.stdout.jsonl")
        stderr_path = os.path.join(self.work, f"{name}-cancel.stderr.txt")
        result = {"terminated": False, "returncode": None, "sent_signal": None}
        try:
            write_config(home, port, SCRIPTED_MODEL_ID, self.lane, self.template,
                         live_model=self.live_model)
            argv = [self.kilo_bin, "run", "--format", "json", "--auto", "-m", SCRIPTED_MODEL_ID,
                    message]
            with open(stdout_path, "w", encoding="utf-8") as out, \
                    open(stderr_path, "w", encoding="utf-8") as err:
                kilo = subprocess.Popen(argv, cwd=workspace, env=self._env(home),
                                        stdin=subprocess.DEVNULL, stdout=out, stderr=err,
                                        start_new_session=True)
                if model_log:
                    self._wait_streaming(model_log, 3, 60)
                before = workspace_hashes(workspace)
                started = time.monotonic()
                signals = []
                for sig, grace in ((signal.SIGINT, 5), (signal.SIGTERM, 5), (signal.SIGKILL, 5)):
                    if kilo.poll() is not None:
                        break
                    result["sent_signal"] = sig.name
                    entry = {"signal": sig.name, "sent_at_ms": _elapsed_ms(started),
                             "exited_after_ms": None}
                    signals.append(entry)
                    os.killpg(kilo.pid, sig)
                    try:
                        kilo.wait(timeout=grace)
                    except subprocess.TimeoutExpired:
                        continue
                    entry["exited_after_ms"] = _elapsed_ms(started)
                result["returncode"] = kilo.poll()
                result["terminated"] = kilo.poll() is not None
                group_empty = _group_empty(kilo.pid)
                after = workspace_hashes(workspace)
                elapsed = _elapsed_ms(started)
                # The model server notices the dropped stream on its next write.
                if model_log:
                    self._wait_disconnect(model_log, 5)
            with open(stdout_path, encoding="utf-8") as fh:
                events, errors = parse_events(fh.read())
            self.parse_errors.extend(f"{name}-cancel {err}" for err in errors)
            requests = read_model_log(model_log)
        finally:
            if proc is not None:
                stop_model(proc)
        streamed = sum(1 for r in requests if r.get("event") == "chunk")
        disconnected = any(r.get("event") == "client_disconnected" for r in requests)
        last = events[-1] if events else {}
        receipt = cancel_receipt(
            signal_sent=result["sent_signal"], signals=signals, pgid=kilo.pid,
            returncode=result["returncode"], terminated=result["terminated"],
            group_empty_after_exit=group_empty, elapsed_ms=elapsed,
            last_event={"type": last.get("type"), "sessionID": last.get("sessionID")}
            if events else None,
            streamed_chunks=streamed, client_disconnected=disconnected,
            before=before, after=after)
        result.update({"events": events, "streamed_chunks": streamed,
                       "client_disconnected": disconnected, "workspace": workspace,
                       "stdout_path": stdout_path, "stderr_path": stderr_path,
                       "session_id": next((e["sessionID"] for e in events
                                           if e.get("sessionID")), None),
                       "launcher_receipt": receipt})
        return result

    @staticmethod
    def _wait_disconnect(model_log, timeout):
        deadline = time.time() + timeout
        while time.time() < deadline:
            if any(r.get("event") == "client_disconnected" for r in read_model_log(model_log)):
                return True
            time.sleep(0.1)
        return False

    @staticmethod
    def _wait_streaming(model_log, count, timeout):
        deadline = time.time() + timeout
        while time.time() < deadline:
            if sum(1 for r in read_model_log(model_log) if r.get("event") == "chunk") >= count:
                return True
            time.sleep(0.1)
        return False


def _tool_for(name):
    if name == "allow":
        return {"name": "write", "arguments": {"filePath": ALLOWED, "content": ALLOWED_CONTENT}}
    if name == "deny":
        return {"name": "write", "arguments": {"filePath": FORBIDDEN, "content": "no\n"}}
    if name == "bash":
        return {"name": "bash", "arguments": {"command": BASH_COMMAND,
                                              "description": "canary forbidden command"}}
    raise ValueError(name)


def run_scenarios(runner):
    """Execute allow, deny, bash-deny, resume and cancel. Returns scenario verdicts."""
    results, evidence = {}, {}
    # allow
    run = runner.run_turn("allow", "run", message="write allowed", tool=_tool_for("allow"))
    files = workspace_state(run["workspace"])
    results["allow"] = judge_allow(run["events"], files)
    results["allow"]["evidence"]["returncode"] = run["returncode"]
    evidence["allow_state"] = files
    # deny: write to a forbidden path, and bash
    run = runner.run_turn("deny", "run", message="write forbidden", tool=_tool_for("deny"))
    files = workspace_state(run["workspace"])
    write_verdict = judge_deny(run["events"], files, filename=FORBIDDEN, permission="edit")
    run_b = runner.run_turn("deny", "bash", message="run forbidden command", tool=_tool_for("bash"))
    files_b = workspace_state(run_b["workspace"])
    bash_verdict = judge_deny(run_b["events"], files_b, filename=BASH_FILE, permission="bash")
    results["deny"] = _combine({"write": write_verdict, "bash": bash_verdict})
    # resume: turn 1 writes allowed.txt; turn 2 is a fresh process on the same session
    first = runner.run_turn("resume", "turn1", message="write allowed", tool=_tool_for("allow"))
    if not first["session_id"]:
        results["resume"] = _fail("first run produced no session id")
    else:
        state1 = workspace_state(first["workspace"])
        second = runner.run_turn("resume", "turn2", message="continue", script="tool-once",
                                 tool=_tool_for("allow"), session=first["session_id"])
        state2 = workspace_state(second["workspace"])
        results["resume"] = judge_resume(
            run1_events=first["events"], run2_events=second["events"],
            run1_session=first["session_id"], run2_session_requested=first["session_id"],
            run2_roles=[r for req in second["requests"][:1] for r in req.get("roles", [])],
            run1_server_pid=first["server_pid"], run2_server_pid=second["server_pid"],
            run2_returncode=second["returncode"],
            run1_identity=identity(first["session_id"], state1),
            run2_identity=identity(second["session_id"] or "", state2))
        results["resume"]["evidence"]["run1_returncode"] = first["returncode"]
    # cancel: real SIGINT mid-stream
    cancel = runner.run_cancel("cancel", message="stream a long answer")
    results["cancel"] = judge_cancel(
        cancel["events"], terminated=cancel["terminated"], returncode=cancel["returncode"],
        sent_signal=cancel["sent_signal"] or "none", streamed_chunks=cancel["streamed_chunks"],
        launcher_receipt=cancel["launcher_receipt"])
    results["cancel"]["evidence"]["launcher_receipt"] = cancel["launcher_receipt"]
    results["cancel"]["evidence"]["client_disconnected"] = cancel["client_disconnected"]
    results["cancel"]["evidence"]["workspace_files"] = sorted(
        workspace_state(cancel["workspace"]))
    return results, cancel


def _combine(parts):
    """Deny has two checks: fail beats gap beats pass."""
    verdicts = {name: part["verdict"] for name, part in parts.items()}
    if "fail" in verdicts.values():
        verdict, reason = "fail", "; ".join(f"{k}: {p['reason']}" for k, p in parts.items()
                                           if p["verdict"] == "fail")
    elif "gap" in verdicts.values():
        verdict, reason = "gap", "; ".join(p["reason"] for p in parts.values()
                                          if p["verdict"] == "gap")
    else:
        verdict, reason = "pass", "; ".join(p["reason"] for p in parts.values())
    return {"verdict": verdict, "reason": reason, "evidence": {k: p for k, p in parts.items()}}


def main(argv=None):
    parser = argparse.ArgumentParser(description="Kilo bounded-task canary (#36)")
    parser.add_argument("--kilo-bin", default=os.environ.get("KILO_BIN", ""),
                        help="path to the pinned kilo binary (node_modules/.bin/kilo)")
    parser.add_argument("--out", default="receipt.json")
    parser.add_argument("--work", default="", help="scratch directory (default: a temp dir)")
    parser.add_argument("--node-dir", default="", help="directory holding node to put on PATH")
    parser.add_argument("--lane", choices=("deterministic", "live"), default="deterministic")
    parser.add_argument("--timeout", type=int, default=120)
    parser.add_argument("--fixtures-out", default="",
                        help="also write scrubbed event streams here (for recording fixtures)")
    args = parser.parse_args(argv)
    if not args.kilo_bin:
        print("error: --kilo-bin or KILO_BIN is required", file=sys.stderr)
        return 2
    kilo_bin = os.path.abspath(args.kilo_bin)
    work = args.work or tempfile.mkdtemp(prefix="kilo-canary-")
    os.makedirs(work, exist_ok=True)
    node_dir = os.path.abspath(args.node_dir) if args.node_dir else ""
    base_env = scenario_env(os.path.join(work, "probe-home"), node_dir)
    os.makedirs(os.path.join(work, "probe-home"), exist_ok=True)
    try:
        version, parser_entry, launcher_entry = kilo_entries(kilo_bin, base_env)
    except SystemExit as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    live_model = None
    if args.lane == "live":
        live_cfg = json.loads(LIVE_CONFIG.read_text(encoding="utf-8"))
        if not live_cfg.get("infisical_identity_id"):
            print("live lane not provisioned (owner action: create Infisical OIDC identity"
                  " for this workflow)", file=sys.stderr)
            return 0
        live_model = live_cfg["model"]
    runner = Runner(kilo_bin, work, node_dir=node_dir, lane=args.lane,
                    live_model=live_model, timeout=args.timeout)
    scenarios, _cancel = run_scenarios(runner)
    if args.fixtures_out:
        _record_fixtures(args.fixtures_out, work, [REPO])
    prov = provenance(lane=args.lane, model=live_model)
    receipt = build_receipt(
        kilo_version=version, parser_entry=parser_entry, launcher_entry=launcher_entry,
        node_version=node_version(base_env), lane=args.lane, provenance=prov,
        scenarios=scenarios, parse_errors=runner.parse_errors,
        generated_at=datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))
    Path(args.out).write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
    verdict = receipt_verdict(receipt)
    print(f"receipt: {args.out} verdict={verdict}")
    for name in SCENARIOS:
        print(f"  {name}: {scenarios[name]['verdict']}")
    return 1 if verdict == "fail" else 0


def _record_fixtures(out_dir, work, replacements_roots):
    """Copy the event streams for fixtures, with host paths replaced by placeholders."""
    os.makedirs(out_dir, exist_ok=True)
    mapping = [(work, "<WORK>")] + [(str(root), "<REPO>") for root in replacements_roots]
    names = {"allow-run.stdout.jsonl": "allow_events.jsonl",
             "deny-run.stdout.jsonl": "deny_events.jsonl",
             "deny-bash.stdout.jsonl": "bash_deny_events.jsonl",
             "resume-turn1.stdout.jsonl": "resume_run1_events.jsonl",
             "resume-turn2.stdout.jsonl": "resume_run2_events.jsonl",
             "cancel-cancel.stdout.jsonl": "cancel_events.jsonl"}
    for source, target in names.items():
        path = os.path.join(work, source)
        if os.path.exists(path):
            text = Path(path).read_text(encoding="utf-8")
            Path(out_dir, target).write_text(scrub(text, mapping), encoding="utf-8")


if __name__ == "__main__":
    sys.exit(main())
