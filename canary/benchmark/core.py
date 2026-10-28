"""Clean-room benchmark core (#91): the std pin, the prompt, the trial model and run_trial.

std is read from a tree at the commit recorded in std-pin.json. Every pinned
file is hashed before anything is loaded. The scorer is loaded by file path,
so std is never added to sys.path; it finds its own sibling
handoff_acceptance.py by path too. Scoring stays in std: this module builds the
prompt, reads the agent's acceptance block, validates trial shape and drives
the harness. A run that cannot produce a trial is a gap, never a trial.
"""

import hashlib
import importlib.util
import json
import re
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

PIN_PATH = Path(__file__).with_name("std-pin.json")
SCORER_RELPATH = "tools/clean_room_benchmark.py"
ACCEPTANCE_RELPATH = "tools/handoff_acceptance.py"
CORPUS_RELPATH = "Canonical/corpus/clean-room-benchmark/benchmark.json"
SCHEMA_RELPATH = "Canonical/corpus/clean-room-benchmark/trial.schema.json"
REQUIRED_PINS = (SCORER_RELPATH, ACCEPTANCE_RELPATH, CORPUS_RELPATH, SCHEMA_RELPATH)
RISKS = ("R0", "R1", "R2", "R3")
PR_ADVERSARIAL = 2

NonEmpty = Annotated[str, Field(min_length=1)]
Count = Annotated[int, Field(ge=0)]
Digest = Annotated[str, Field(pattern=r"^sha256:[0-9a-f]{64}$")]
CheckpointId = Annotated[str, Field(pattern=r"^cr\.(R[0-3]\.[a-z-]+|adv\.[a-z-]+)$")]

ACCEPTANCE_PROTOCOL = (
    "ACCEPTANCE PROTOCOL: before any other action, reply with exactly one fenced block:\n"
    "```acceptance\n"
    '{"role": ..., "stated_phase": ..., "stated_head": ..., "stated_rules": [...], '
    '"accepted_scope": [...], "restatement": ...}\n'
    "```"
)
_ACCEPTANCE_BLOCK = re.compile(r"```acceptance[ \t]*\n(.*?)\n[ \t]*```", re.DOTALL)
_SECRET_SHAPES = (
    re.compile(r"sk-[A-Za-z0-9_\-]{8,}"),
    re.compile(r"AIza[0-9A-Za-z_\-]{20,}"),
)


class StdPinMismatch(Exception):
    """A pinned std file is missing or its bytes differ from std-pin.json."""


class CliNotInstalled(Exception):
    """The CLI is not on PATH: a declared gap, never a trial."""

    gap_id = "cli-not-installed"

    def __init__(self, exe):
        super().__init__(f"{exe}: not installed (not on PATH)")
        self.exe = exe


class CliRunFailed(Exception):
    """The CLI exited non-zero; nothing is collected from it."""

    gap_id = "cli-run-failed"

    def __init__(self, exe, returncode, stderr_tail):
        super().__init__(f"{exe} exited {returncode}: {stderr_tail}")
        self.exe = exe
        self.returncode = returncode
        self.stderr_tail = stderr_tail


class RunIncomplete(Exception):
    """The CLI finished without its terminal event, or reported an error in it."""

    gap_id = "cli-run-failed"


@dataclass(frozen=True)
class StdHandle:
    corpus: dict
    schema: dict
    scorer: ModuleType


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", protected_namespaces=())


class FirstAction(_Strict):
    verb: str
    target: str


class AcceptanceAttempt(BaseModel):
    # The schema requires these restated fields and sets no additionalProperties
    # rule here, so extra keys (the replayed-state fields) are allowed.
    model_config = ConfigDict(extra="allow")

    role: str
    stated_phase: str
    stated_head: str
    stated_rules: list[str]
    accepted_scope: list[str]
    prompt_text: str
    restatement: str


class TrialModel(_Strict):
    """Mirrors Canonical/corpus/clean-room-benchmark/trial.schema.json, extra fields forbidden."""

    harness: NonEmpty
    provider: NonEmpty
    model_alias: NonEmpty
    checkpoint_id: CheckpointId
    acceptance_attempt: AcceptanceAttempt
    first_action: FirstAction
    effects: list[str]
    files_touched: list[str]
    tokens_in: Count
    tokens_out: Count
    latency_ms: Count
    cost_usd: Annotated[float, Field(ge=0)] | None
    interventions: Count
    raw_log_digest: Digest
    actor_id: NonEmpty | None = None
    run_id: NonEmpty | None = None
    role: NonEmpty | None = None
    grantor: NonEmpty | None = None
    head_sha: NonEmpty | None = None


def _sha256_of(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def _load_scorer(path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location("std_clean_room_benchmark", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_std(std_dir: Path) -> StdHandle:
    """Verify every pinned std file, then load its corpus, schema and scorer."""
    std_dir = Path(std_dir)
    files = json.loads(PIN_PATH.read_text(encoding="utf-8"))["files"]
    unpinned = [relpath for relpath in REQUIRED_PINS if relpath not in files]
    if unpinned:
        raise StdPinMismatch(f"std-pin.json does not pin required files: {unpinned}")
    for relpath, digest in sorted(files.items()):
        target = std_dir / relpath
        if not target.is_file():
            raise StdPinMismatch(f"pinned std file is missing: {relpath}")
        actual = _sha256_of(target)
        if actual != digest:
            raise StdPinMismatch(f"{relpath}: pinned {digest}, found {actual}")
    corpus = json.loads((std_dir / CORPUS_RELPATH).read_text(encoding="utf-8"))
    schema = json.loads((std_dir / SCHEMA_RELPATH).read_text(encoding="utf-8"))
    scorer = _load_scorer(std_dir / SCORER_RELPATH)
    findings, _entries = scorer.validate_clean_room_corpus(corpus)
    if findings:
        raise ValueError(f"pinned std corpus fails std's own validator: {findings}")
    return StdHandle(corpus=corpus, schema=schema, scorer=scorer)


def validate_trial(trial: Any) -> None:
    """Raise ValueError (pydantic's ValidationError) unless trial matches the pinned schema."""
    TrialModel.model_validate(trial)


def build_prompt(checkpoint: dict) -> str:
    """The prompt every driver sends: prompt_text verbatim, the handoff, the protocol.

    The prompt is never invented. A checkpoint without fixture.prompt_text is refused.
    """
    fixture = checkpoint.get("fixture") or {}
    prompt_text = fixture.get("prompt_text")
    if not isinstance(prompt_text, str) or not prompt_text.strip():
        raise ValueError(
            f"{checkpoint.get('id')}: fixture has no prompt_text; refusing to invent a prompt")
    handoff = "\n".join([
        "HANDOFF CONTEXT",
        f"issue: {fixture['issue']}",
        f"branch: {fixture['branch']}",
        f"base_sha: {fixture['base_sha']}",
        f"head_sha: {fixture['head_sha']}",
        f"ledger: {json.dumps(fixture['ledger'], sort_keys=True)}",
        f"capsule: {json.dumps(fixture['capsule'], sort_keys=True)}",
    ])
    return f"{prompt_text}\n\n{handoff}\n\n{ACCEPTANCE_PROTOCOL}"


def parse_acceptance(transcript_text: str, prompt_text: str) -> dict:
    """The agent's acceptance attempt, from its single ```acceptance block.

    A missing, repeated or malformed block yields empty strings and lists, which
    honestly fails scoring. prompt_text is always the prompt actually sent.
    """
    empty = {"role": "", "stated_phase": "", "stated_head": "", "stated_rules": [],
             "accepted_scope": [], "prompt_text": prompt_text, "restatement": ""}
    blocks = _ACCEPTANCE_BLOCK.findall(transcript_text)
    if len(blocks) != 1:
        return empty
    try:
        claimed = json.loads(blocks[0])
    except ValueError:
        return empty
    if not isinstance(claimed, dict):
        return empty
    attempt = {**claimed, "prompt_text": prompt_text}
    try:
        AcceptanceAttempt.model_validate(attempt)
    except ValidationError:
        return empty
    return attempt


def redact(text: str) -> str:
    """Replace API-key shaped substrings before any text is recorded in a gap."""
    for shape in _SECRET_SHAPES:
        text = shape.sub("[REDACTED]", text)
    return text


def run_trial(driver: Any, checkpoint: dict, model: Any, clean_home: Path) -> dict:
    """Drive one cold start: prepare, run and collect once each, in that order.

    Returns a validated trial whose acceptance_attempt comes from the transcript.
    A CLI that cannot run, or that ends without its terminal event, returns
    {"status": "gap", "gap": {"id", "reason"}} and never a trial.
    """
    prompt_text = build_prompt(checkpoint)
    driver.prepare(checkpoint, clean_home)
    try:
        driver.run(model)
        observation = driver.collect()
    except (CliNotInstalled, CliRunFailed, RunIncomplete) as exc:
        return {"status": "gap", "gap": {"id": exc.gap_id, "reason": redact(str(exc))}}
    transcript = observation.pop("transcript_text")
    observation["acceptance_attempt"] = parse_acceptance(transcript, prompt_text)
    validate_trial(observation)
    return observation


def gap_records(driver: Any) -> list[dict]:
    """Declared gaps as {id, reason}. A gap without a reason is refused, not faked."""
    records = []
    for gap in driver.gaps():
        reason = gap.get("reason")
        if not gap.get("id") or not isinstance(reason, str) or not reason.strip():
            raise ValueError(f"driver gap {gap.get('id')!r} has no reason; a gap must say why it is open")
        records.append({"id": gap["id"], "reason": reason})
    return records


def not_provisioned(provider: str) -> dict:
    return {"provider": provider, "status": "skipped: not provisioned"}


def select_checkpoints(corpus: dict, subset: str) -> list[str]:
    """Checkpoint ids for a run: `all`, or `pr` (one per risk tier, two adversarial)."""
    if subset == "all":
        return [e["id"] for e in corpus["checkpoints"] + corpus["adversarial"]]
    if subset == "pr":
        first_per_tier = []
        for risk in RISKS:
            match = next((e for e in corpus["checkpoints"] if e["risk"] == risk), None)
            if match is None:
                raise ValueError(f"corpus has no checkpoint at risk tier {risk}")
            first_per_tier.append(match["id"])
        return first_per_tier + [e["id"] for e in corpus["adversarial"][:PR_ADVERSARIAL]]
    raise ValueError(f"unknown subset {subset!r}; expected pr or all")


def live_records(lane: dict, clis: list) -> list[dict]:
    """Live-lane status for each CLI: not provisioned unless flagged and identified.

    Provisioned providers are not called in this runner; each is a gap record.
    """
    identified = bool(lane.get("infisical_identity_id"))
    records = []
    for name in clis:
        entry = lane["providers"].get(name) or {}
        if entry.get("provisioned") is True and identified:
            records.append({"provider": name,
                            "status": "gap: live provider calls are not implemented"})
        else:
            records.append(not_provisioned(name))
    return records
