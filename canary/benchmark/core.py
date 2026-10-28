"""Clean-room benchmark core (#91): the std pin, the trial model and run_trial.

std is read from a tree at the commit recorded in std-pin.json. Every pinned
file is hashed before anything is loaded. The scorer is loaded by file path,
so std is never added to sys.path; it finds its own sibling
handoff_acceptance.py by path too. Scoring stays in std: this module validates
trial shape and drives the harness, nothing more.
"""

import hashlib
import importlib.util
import json
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field

PIN_PATH = Path(__file__).with_name("std-pin.json")
SCORER_RELPATH = "tools/clean_room_benchmark.py"
ACCEPTANCE_RELPATH = "tools/handoff_acceptance.py"
CORPUS_RELPATH = "Canonical/corpus/clean-room-benchmark/benchmark.json"
SCHEMA_RELPATH = "Canonical/corpus/clean-room-benchmark/trial.schema.json"
REQUIRED_PINS = (SCORER_RELPATH, ACCEPTANCE_RELPATH, CORPUS_RELPATH, SCHEMA_RELPATH)

NonEmpty = Annotated[str, Field(min_length=1)]
Count = Annotated[int, Field(ge=0)]
Digest = Annotated[str, Field(pattern=r"^sha256:[0-9a-f]{64}$")]
CheckpointId = Annotated[str, Field(pattern=r"^cr\.(R[0-3]\.[a-z-]+|adv\.[a-z-]+)$")]


class StdPinMismatch(Exception):
    """A pinned std file is missing or its bytes differ from std-pin.json."""


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


def run_trial(driver: Any, checkpoint: dict, model: Any, clean_home: Path) -> dict:
    """Drive one cold start: prepare, run and collect once each, in that order.

    The collected trial is validated and returned unchanged.
    """
    driver.prepare(checkpoint, clean_home)
    driver.run(model)
    trial = driver.collect()
    validate_trial(trial)
    return trial


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
