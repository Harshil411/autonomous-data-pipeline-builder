"""UI-test fixtures.

Colima (the Docker runtime on macOS) only shares the user's home folder with its VM, and
pytest's default temp folder lives under /private/var. Containers would see empty mounts,
so integration tests get temp folders inside the repository's ``var/`` instead - the same
place real runs keep their workspaces.
"""

import shutil
import uuid
from pathlib import Path

import pytest

from adpb.contracts import DebugPatch
from adpb.llm.scripted import ScriptedModel
from adpb.observability.events import EventLog
from adpb.runtime import RunHandle
from tests.integration.pipeline_fixtures import (
    CODE_ORDERS,
    CODE_ORDERS_BUGGY,
    CUSTOMERS_CSV,
    ORDERS_CSV,
    happy_script,
    request,
)

REPO = Path(__file__).resolve().parents[2]


@pytest.fixture
def tmp_path():
    path = REPO / "var" / "pytest" / uuid.uuid4().hex[:12]
    path.mkdir(parents=True)
    yield path
    shutil.rmtree(path, ignore_errors=True)


@pytest.fixture(scope="session")
def recorded(tmp_path_factory):
    """The event log of one scripted run: clean_orders fails once, the Debugger patches it,
    a visitor approves and the rows are loaded. Shared by the view-model and graph tests."""
    tmp = tmp_path_factory.mktemp("ui")
    (tmp / "customers.csv").write_text(CUSTOMERS_CSV)
    (tmp / "orders.csv").write_text(ORDERS_CSV)
    script = happy_script()
    script["coder"][1] = CODE_ORDERS_BUGGY
    script["debugger"] = [DebugPatch(diagnosis="dataset is raw.orders", code=CODE_ORDERS.code)]
    h = RunHandle.create(request(), [tmp / "customers.csv", tmp / "orders.csv"],
                         home=tmp / "var", model=ScriptedModel(script),
                         approval_secret="ui-secret-xxxxxx", sandbox="local", embeddings="hash")
    h.run()
    h.decide(approved=True, by="Booth visitor")
    return EventLog.read(h.ws.events_path)
