"""Pytest fixtures: isolated SQLite DB + in-process center agents.

Env must be set BEFORE importing app modules (config is read at import time).
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
_TMP = tempfile.mkdtemp(prefix="platform-test-")

os.environ["DATABASE_URL"] = f"sqlite:///{_TMP}/test.db"
os.environ["SEED_ON_STARTUP"] = "0"
os.environ["RUN_EXECUTION"] = "sync"
os.environ["SMALL_CELL_K"] = "5"

sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT / "center_agent"))

import pytest
from fastapi.testclient import TestClient

from center_agent.datagen import generate_center  # noqa: E402
from center_agent.eventgen import generate_center_events  # noqa: E402

from app import agents  # noqa: E402
from app.db import Base, SessionLocal, engine  # noqa: E402
from app.main import app  # noqa: E402
from app.seed import seed  # noqa: E402

DATA_ROOT = Path(_TMP) / "centers"


def _gen_data() -> None:
    if DATA_ROOT.exists():
        return
    for code in ["C1", "C2", "C3", "C4", "C5", "C6"]:
        generate_center(code, DATA_ROOT)
        generate_center_events(code, DATA_ROOT)


@pytest.fixture()
def client():
    _gen_data()
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    local = agents.LocalAgentClient(str(DATA_ROOT))
    agents.set_agent_client(local)
    with SessionLocal() as db:
        seed(db, client=local, wait=False)
    with TestClient(app) as c:
        yield c


def login(client: TestClient, username: str, password: str) -> dict:
    r = client.post("/api/auth/login", json={"username": username, "password": password})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['token']}"}


@pytest.fixture()
def admin(client):
    return login(client, "admin", "admin123")


@pytest.fixture()
def researcher1(client):
    return login(client, "researcher1", "research123")


@pytest.fixture()
def researcher2(client):
    return login(client, "researcher2", "research123")


@pytest.fixture()
def reviewer(client):
    return login(client, "reviewer", "review123")


@pytest.fixture()
def adjudicator1(client):
    return login(client, "adjudicator1", "adjudicate123")


@pytest.fixture()
def adjudicator2(client):
    return login(client, "adjudicator2", "adjudicate123")


@pytest.fixture()
def chief(client):
    return login(client, "chief", "adjudicate123")


def run_payload(rule_version_id: int, with_lab: bool = False) -> dict:
    metrics = [
        {"type": "count"},
        {"type": "outcome_count", "variable": "followup.outcome", "value": "remission"},
    ]
    if with_lab:
        metrics.append({"type": "mean", "variable": "followup.lab_value"})
    return {
        "rule_version_id": rule_version_id,
        "request_spec": {"group_by": ["treatment.path"], "metrics": metrics},
    }
