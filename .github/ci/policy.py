"""PR policy: every PR closes an Issue and comes from an issue branch.

Reads ``PR_BODY`` and ``HEAD_REF``; mirrors the Agent Engineering
Standard's thin-Issue rule (``issue/<n>-<slug>`` plus a closing keyword).
"""

from __future__ import annotations

import os
import re
import sys

CLOSING = re.compile(r"(?i)\b(close[sd]?|fix(e[sd])?|resolve[sd]?)\s+#\d+\b")
BRANCH = re.compile(r"^issue/\d+-[a-z0-9][a-z0-9-]*$")


def check(body, ref):
    problems = []
    if not CLOSING.search(body or ""):
        problems.append("PR body needs a closing keyword line such as 'Closes #<issue>'")
    if not BRANCH.match(ref or ""):
        problems.append("head branch %r does not match issue/<n>-<slug>" % ref)
    return problems


def main():
    problems = check(os.environ.get("PR_BODY", ""), os.environ.get("HEAD_REF", ""))
    for problem in problems:
        print("ERROR:", problem)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
