"""Integration-test fixtures.

Colima (the Docker runtime on macOS) only shares the user's home folder with its VM, and
pytest's default temp folder lives under /private/var. Containers would see empty mounts,
so integration tests get temp folders inside the repository's ``var/`` instead - the same
place real runs keep their workspaces.
"""

import shutil
import uuid
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]


@pytest.fixture
def tmp_path():
    path = REPO / "var" / "pytest" / uuid.uuid4().hex[:12]
    path.mkdir(parents=True)
    yield path
    shutil.rmtree(path, ignore_errors=True)
