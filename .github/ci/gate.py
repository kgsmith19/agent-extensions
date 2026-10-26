"""Aggregate PR Gate: pass only when every expected job concluded success.

Reads the workflow's ``needs`` context (``NEEDS``, JSON) and the
space-separated job list it must cover (``EXPECTED_JOBS``). Failure,
cancelled, skipped, neutral, an empty result, a missing job, or an
unexpected extra job all fail the gate.
"""

from __future__ import annotations

import json
import os
import sys


def evaluate(needs, expected):
    problems = []
    missing = sorted(set(expected) - set(needs))
    extra = sorted(set(needs) - set(expected))
    if missing:
        problems.append("expected job(s) missing from needs: %s" % ", ".join(missing))
    if extra:
        problems.append("unexpected job(s) in needs: %s" % ", ".join(extra))
    for job in sorted(set(needs) & set(expected)):
        result = needs[job].get("result")
        if result != "success":
            problems.append("job %s concluded %r, not 'success'" % (job, result))
    return problems


def main():
    needs = json.loads(os.environ["NEEDS"])
    expected = os.environ["EXPECTED_JOBS"].split()
    for job in sorted(needs):
        print("%-10s %s" % (job, needs[job].get("result")))
    problems = evaluate(needs, expected)
    for problem in problems:
        print("ERROR:", problem)
    print("Gate %s" % ("FAILED" if problems else "PASSED"))
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
