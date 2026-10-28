"""Standard at the pinned commit, for the clean-room benchmark tests (#91).

The benchmark pins Standard at PIN_COMMIT. Tests never read a working tree:
they extract that exact commit with `git archive`, which only reads the source
checkout. The source is AES_STANDARD_DIR when set (CI), else the container
checkout. A missing or unarchivable source fails the test; it never skips.
"""

import importlib.util
import io
import os
import subprocess
import tarfile
from pathlib import Path

import pytest

PIN_COMMIT = "e397784d1358524bd507d71fb3f6b1fd336005e1"
DEFAULT_STD_CHECKOUT = Path("/home/user/agent-engineering-standard")

# Std files the runner pins and loads. handoff_acceptance.py is loaded by
# clean_room_benchmark.py from its sibling path, so it is pinned too.
PINNED_FILES = (
    "tools/clean_room_benchmark.py",
    "tools/handoff_acceptance.py",
    "Canonical/corpus/clean-room-benchmark/benchmark.json",
    "Canonical/corpus/clean-room-benchmark/trial.schema.json",
)
SCHEMA_RELPATH = "Canonical/corpus/clean-room-benchmark/trial.schema.json"


def std_source() -> Path:
    env = os.environ.get("AES_STANDARD_DIR")
    return Path(env) if env else DEFAULT_STD_CHECKOUT


@pytest.fixture(scope="session")
def std_tree(tmp_path_factory) -> Path:
    """A std tree at PIN_COMMIT, extracted from git objects (read-only source)."""
    source = std_source()
    result = subprocess.run(
        ["git", "-C", str(source), "archive", "--format=tar", PIN_COMMIT],
        capture_output=True, check=False)
    if result.returncode != 0:
        pytest.fail(
            "cannot archive std at %s from %s (set AES_STANDARD_DIR to a "
            "checkout that contains it): %s"
            % (PIN_COMMIT, source, result.stderr.decode("utf-8", "replace").strip()))
    dest = tmp_path_factory.mktemp("std-pinned")
    with tarfile.open(fileobj=io.BytesIO(result.stdout)) as tar:
        tar.extractall(dest, filter="data")
    return dest


@pytest.fixture(scope="session")
def std_module(std_tree):
    """The pinned std scorer, loaded by path, independent of canary.benchmark."""
    path = std_tree / "tools" / "clean_room_benchmark.py"
    spec = importlib.util.spec_from_file_location("std_clean_room_under_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module
