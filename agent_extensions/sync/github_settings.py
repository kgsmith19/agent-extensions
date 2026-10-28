"""ext#97 dispatch-only GitHub settings apply (RED skeleton).

Every public name below raises NotImplementedError until the GREEN commit.
"""

import sys
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, TextIO


class SchemaError(ValueError):
    """Declared settings failed closed validation."""


@dataclass(frozen=True)
class Change:
    op: str  # create | update | same | extra
    kind: str  # repo | ruleset | label | variable
    name: str


class GitHubApi:
    def __init__(self, base_url: str, token: str):
        self.base_url = base_url.rstrip("/")
        self.token = token

    def request(self, method: str, path: str, payload: Optional[Dict] = None):
        raise NotImplementedError


def validate(doc: Any) -> Dict[str, Any]:
    raise NotImplementedError


def read_live(api: Any, repo: str) -> Dict[str, Any]:
    raise NotImplementedError


def plan(declared: Dict[str, Any], live: Dict[str, Any]) -> List[Change]:
    raise NotImplementedError


def apply_changes(
    api: Any, repo: str, declared: Dict[str, Any], live: Dict[str, Any], changes: List[Change]
) -> None:
    raise NotImplementedError


def run_sync(api: Any, repo: str, declared: Dict[str, Any], apply: bool) -> List[Change]:
    raise NotImplementedError


def main(argv: Optional[List[str]] = None, api: Any = None, out: Optional[TextIO] = None) -> int:
    raise NotImplementedError


if __name__ == "__main__":
    sys.exit(main())
