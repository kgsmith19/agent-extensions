"""Provider-neutral session continuity capsules.

A *capsule* is a small, human-readable record of where work stopped: task,
phase, branch/head, next action, blockers, decisions, evidence. It lives
outside any provider's config (``~/.agent-state/capsules``) so every harness
reads and writes the same record.

Harness adapters are deliberately thin: they call this module on session
start (render/inject) and before compaction or shutdown (capture). Nothing
here imports or assumes Claude, Codex, Antigravity, or pi.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

CAPSULE_VERSION = 1

#: Neutral store. Override with AGENT_CAPSULE_DIR for tests or multi-user hosts.
DEFAULT_CAPSULE_DIR = Path(
    os.environ.get("AGENT_CAPSULE_DIR") or (Path.home() / ".agent-state" / "capsules")
)

_HARNESS_ENV = (
    # (env var, label) — first match wins; provider-neutral detection.
    ("PI_CODING_AGENT", "pi"),
    ("CLAUDECODE", "claude"),
    ("CLAUDE_CODE", "claude"),
    ("CODEX_SANDBOX", "codex"),
    ("ANTIGRAVITY", "antigravity"),
)
_MODEL_ENV = ("PI_MODEL", "CLAUDE_MODEL", "ANTHROPIC_MODEL", "OPENAI_MODEL")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _git(cwd: Path, *args: str) -> Optional[str]:
    try:
        proc = subprocess.run(
            ["git", "-C", str(cwd), *args],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    return proc.stdout.strip()


def detect_git(cwd: Path) -> dict:
    """Return neutral git facts for ``cwd``; empty dict when not a repo."""
    root = _git(cwd, "rev-parse", "--show-toplevel")
    if not root:
        return {}
    status = _git(cwd, "status", "--porcelain") or ""
    return {
        "repo": root.replace("\\", "/"),
        "branch": _git(cwd, "rev-parse", "--abbrev-ref", "HEAD") or "",
        "head": _git(cwd, "rev-parse", "HEAD") or "",
        "dirty": len([ln for ln in status.splitlines() if ln.strip()]),
    }


def detect_harness() -> str:
    for env, label in _HARNESS_ENV:
        if os.environ.get(env):
            return label
    return ""


def detect_model() -> str:
    for env in _MODEL_ENV:
        if os.environ.get(env):
            return os.environ[env]
    return ""


def project_key(cwd: Path) -> str:
    """Stable, human-readable key: repo dir name, disambiguated by path hash."""
    git = detect_git(cwd)
    base = Path(git["repo"]).name if git.get("repo") else cwd.name
    slug = re.sub(r"[^a-zA-Z0-9._-]+", "-", base).strip("-") or "workspace"
    return slug


def capsule_path(cwd: Path, directory: Optional[Path] = None) -> Path:
    return (directory or DEFAULT_CAPSULE_DIR) / f"{project_key(cwd)}.json"


@dataclass
class Capsule:
    """One continuity record. Auto fields are refreshed; manual fields persist."""

    version: int = CAPSULE_VERSION
    project: str = ""
    cwd: str = ""
    repo: str = ""
    branch: str = ""
    head: str = ""
    dirty: int = 0
    task: str = ""
    phase: str = ""
    next_action: str = ""
    blockers: List[str] = field(default_factory=list)
    decisions: List[str] = field(default_factory=list)
    evidence: List[str] = field(default_factory=list)
    open_issues: List[int] = field(default_factory=list)
    harness: str = ""
    model: str = ""
    updated_at: str = ""

    @classmethod
    def load(cls, path: Path) -> "Capsule":
        if not path.exists():
            return cls()
        data = json.loads(path.read_text(encoding="utf-8"))
        known = set(cls.__dataclass_fields__)
        return cls(**{k: v for k, v in data.items() if k in known})

    def save(self, path: Path) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(asdict(self), indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        return path

    def is_empty(self) -> bool:
        return not any(
            (self.task, self.next_action, self.phase, self.branch, self.blockers)
        )

    def render(self, fmt: str = "plain") -> str:
        """Render for injection. Unknown/empty capsules render to an empty string."""
        if self.is_empty():
            return ""
        loc = f"{self.branch or '?'} @ {(self.head or '')[:10]}"
        if self.dirty:
            loc += f" (+{self.dirty} dirty)"
        lines = [f"## Continuity capsule - {self.project or self.cwd}"]
        meta = [f"phase: {self.phase}" if self.phase else "", f"git: {loc}" if self.branch or self.head else ""]
        lines.append(" | ".join(m for m in meta if m))
        if self.task:
            lines.append(f"- task: {self.task}")
        if self.next_action:
            lines.append(f"- next action: {self.next_action}")
        if self.blockers:
            lines.append(f"- blockers: {'; '.join(self.blockers)}")
        if self.decisions:
            lines.append(f"- decisions: {'; '.join(self.decisions)}")
        if self.evidence:
            lines.append(f"- evidence: {'; '.join(self.evidence)}")
        if self.open_issues:
            lines.append(f"- open issues: {', '.join('#' + str(i) for i in self.open_issues)}")
        if self.updated_at:
            by = "/".join(p for p in (self.harness, self.model) if p)
            lines.append(f"- updated: {self.updated_at}" + (f" ({by})" if by else ""))
        text = "\n".join(lines)

        if fmt == "claude":
            payload = {
                "hookSpecificOutput": {
                    "hookEventName": "SessionStart",
                    "additionalContext": text,
                }
            }
            return json.dumps(payload)
        return text


def refresh(cwd: Path, *, harness: str = "", model: str = "", directory: Optional[Path] = None) -> Capsule:
    """Load-or-create the capsule for ``cwd`` and refresh its auto fields."""
    path = capsule_path(cwd, directory)
    cap = Capsule.load(path)
    git = detect_git(cwd)
    cap.version = CAPSULE_VERSION
    cap.project = project_key(cwd)
    cap.cwd = str(cwd).replace("\\", "/")
    if git:
        cap.repo = git["repo"]
        cap.branch = git["branch"]
        cap.head = git["head"]
        cap.dirty = git["dirty"]
    cap.harness = harness or detect_harness() or cap.harness
    cap.model = model or detect_model() or cap.model
    cap.updated_at = _now()
    return cap
