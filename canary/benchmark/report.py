"""Clean-room benchmark report (#91): std's aggregate, plus status and per-checkpoint rows.

Aggregation is std's report(); per-trial grading is std's score(). This module
only adds what std's output lacks: the skipped-provider status records, a row
for every checkpoint id, and a markdown rendering of those numbers.
"""

import json
from pathlib import Path
from typing import Any


def _entries(handle: Any) -> list[dict]:
    return list(handle.corpus["checkpoints"]) + list(handle.corpus["adversarial"])


def _per_checkpoint(trials: list, handle: Any) -> list[dict]:
    """One row per corpus entry, in corpus order, with std-scored success counts."""
    entries = _entries(handle)
    by_id = {entry["id"]: entry for entry in entries}
    rows = {entry["id"]: {"id": entry["id"], "trials": 0, "successes": 0} for entry in entries}
    for trial in trials:
        if not isinstance(trial, dict):
            continue
        entry = by_id.get(trial.get("checkpoint_id"))
        if entry is None:
            continue
        rows[entry["id"]]["trials"] += 1
        if handle.scorer.score(trial, entry)["success"]:
            rows[entry["id"]]["successes"] += 1
    return [rows[entry["id"]] for entry in entries]


def build_report(trials: list, handle: Any, status: list | None = None) -> dict:
    """std's report() JSON, unchanged, plus status records and per-checkpoint rows."""
    _markdown, json_text = handle.scorer.report(trials, handle.corpus)
    doc = json.loads(json_text)
    doc["status"] = list(status or [])
    doc["per_checkpoint"] = _per_checkpoint(trials, handle)
    return doc


def _fmt(value: Any) -> str:
    if value is None:
        return "n/a"
    if isinstance(value, float):
        return f"{value:g}"
    return str(value)


def _markdown(report: dict) -> str:
    lines = [
        "# Clean-room benchmark report",
        "",
        (f"Trials: {report['trials']} (malformed: {report['malformed_trials']}) "
         f"across {report['checkpoints']} checkpoints and {report['adversarial']} "
         "adversarial fixtures."),
        "",
        "## Groups",
        "",
        "| Harness | Provider | Role | Trials | Success | First action |",
        "|---|---|---|---|---|---|",
    ]
    for group in report["groups"]:
        cells = [group["harness"], group["provider"], group["role"] or "-",
                 _fmt(group["trials"]), _fmt(group["success_rate"]),
                 _fmt(group["first_action_accuracy"])]
        lines.append("| " + " | ".join(cells) + " |")
    lines += ["", "## Cross-provider disagreements", ""]
    if not report["disagreements"]:
        lines.append("None.")
    for item in report["disagreements"]:
        detail = "; ".join(f"{provider}: {', '.join(item['actions'][provider])}"
                           for provider in item["actions"])
        lines.append(f"- `{item['checkpoint_id']}`: {detail}")
    lines += ["", "## Status", ""]
    if not report["status"]:
        lines.append("None.")
    for record in report["status"]:
        lines.append("- " + ", ".join(f"{key}: {value}" for key, value in record.items()))
    lines += ["", "## Checkpoints", "",
              "| Checkpoint | Trials | Successes |", "|---|---|---|"]
    for row in report["per_checkpoint"]:
        lines.append(f"| {row['id']} | {row['trials']} | {row['successes']} |")
    return "\n".join(lines) + "\n"


def write_report(report: dict, out_dir: Path) -> tuple[Path, Path]:
    """Write report.json and report.md into out_dir; return both paths."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    json_path = out / "report.json"
    md_path = out / "report.md"
    json_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    md_path.write_text(_markdown(report), encoding="utf-8")
    return json_path, md_path
