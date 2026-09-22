#!/usr/bin/env python
"""Self-contained launcher for the provider-neutral continuity CLI.

Lets any harness (or a human) call the capsule CLI from any working
directory without setting PYTHONPATH:

    python agent_extensions/continuity/adapters/capsule.py render --cwd .
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from agent_extensions.continuity.__main__ import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
