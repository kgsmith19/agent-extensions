"""Clean-room benchmark runner (#91): python -m canary.benchmark.

  --lane deterministic  each selected CLI runs against its scripted double. A CLI
                        not on PATH is a cli-not-installed gap, never a trial.
  --lane live           live-lane.json decides providers. Every provider not
                        provisioned is recorded as not provisioned. Live calls are
                        not implemented here, so a provisioned provider is a gap.

Writes trials.json, report.json and report.md into --out. Exit 0 when the run
completes, gaps included. Exit 1 on a StdPinMismatch. A crash exits non-zero.
"""

import argparse
import json
import sys
import tempfile
from pathlib import Path

from canary.benchmark import core, report
from canary.benchmark.adapters import DRIVERS
from canary.benchmark.lanes.scripted import (
    ChatCompletionsDouble,
    GenerateContentDouble,
    MessagesDouble,
    ResponsesDouble,
)

LIVE_LANE_PATH = Path(__file__).with_name("live-lane.json")
DOUBLES = {
    "claude-code": MessagesDouble,
    "codex": ResponsesDouble,
    "gemini-cli": GenerateContentDouble,
    "kilo": ChatCompletionsDouble,
}
SCRIPTED_ALIAS = "scripted/m1"
SCRIPT_REPLIES = 8  # more than any CLI needs for one trial; an exhausted script is an error
DEFAULT_CLIS = ",".join(DRIVERS)


def _restatement(checkpoint):
    """The faithful restatement of the capsule, as the scripted reply's acceptance block."""
    capsule = checkpoint["fixture"]["capsule"]
    action = capsule["next_action"]
    block = {
        "role": "builder",
        "stated_phase": capsule["phase"],
        "stated_head": capsule["head"],
        "stated_rules": list(capsule["rule_ids"]),
        "accepted_scope": list(capsule["scope"]),
        "restatement": f"{action['verb']}:{action['target']}",
    }
    return "```acceptance\n" + json.dumps(block) + "\n```"


def _deterministic_trial(cli, checkpoint):
    driver = DRIVERS[cli]()
    replies = [_restatement(checkpoint)] * SCRIPT_REPLIES
    with DOUBLES[cli](replies) as double:
        model = {"alias": SCRIPTED_ALIAS, "base_url": double.base_url}
        with tempfile.TemporaryDirectory(prefix="clean-room-") as home:
            return core.run_trial(driver, checkpoint, model, Path(home))


def _run(args):
    handle = core.load_std(Path(args.std_dir))
    corpus = handle.corpus
    entries = {e["id"]: e for e in corpus["checkpoints"] + corpus["adversarial"]}
    clis = [name.strip() for name in args.clis.split(",") if name.strip()]
    trials, status = [], []
    if args.lane == "deterministic":
        for cli in clis:
            status.extend({"harness": cli, **record}
                          for record in core.gap_records(DRIVERS[cli]()))
            for checkpoint_id in core.select_checkpoints(corpus, args.subset):
                outcome = _deterministic_trial(cli, entries[checkpoint_id])
                if outcome.get("status") == "gap":
                    status.append({"harness": cli, "checkpoint_id": checkpoint_id,
                                   **outcome["gap"]})
                else:
                    trials.append(outcome)
    else:
        lane = json.loads(LIVE_LANE_PATH.read_text(encoding="utf-8"))
        status.extend(core.live_records(lane, clis))
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "trials.json").write_text(json.dumps(trials, indent=2, sort_keys=True) + "\n",
                                     encoding="utf-8")
    report.write_report(report.build_report(trials, handle, status=status), out)
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(prog="python -m canary.benchmark")
    parser.add_argument("--lane", required=True, choices=("deterministic", "live"))
    parser.add_argument("--subset", required=True, choices=("pr", "all"))
    parser.add_argument("--std-dir", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--clis", default=DEFAULT_CLIS)
    args = parser.parse_args(argv)
    unknown = [c for c in args.clis.split(",") if c.strip() and c.strip() not in DRIVERS]
    if unknown:
        parser.error(f"unknown CLI(s): {unknown}; expected a subset of {sorted(DRIVERS)}")
    try:
        return _run(args)
    except core.StdPinMismatch as exc:
        print(f"StdPinMismatch: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
