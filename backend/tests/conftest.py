"""Shared fixtures. Integration tests use a separate database: ``<MONGODB_DB>_test``."""
from __future__ import annotations

import os

import pytest

os.environ.setdefault("GRIDMIND_DISABLE_LLM", "1")  # deterministic tests, no API spend

from app.config import SETTINGS  # noqa: E402
from app.db.mongo import get_db  # noqa: E402
from app.db.repositories import Repository  # noqa: E402
from app.seed.cigre_dc import build_cigre_dc_circuit, demo_scenarios  # noqa: E402

TEST_DB = f"{SETTINGS.mongodb_db}_test"


def pytest_configure(config):
    config.addinivalue_line("markers", "integration: requires MongoDB")


@pytest.fixture(scope="session")
def equipment() -> list[dict]:
    _, eqs = build_cigre_dc_circuit()
    return eqs


@pytest.fixture(scope="session")
def peak_conditions() -> dict:
    return demo_scenarios()[0]["conditions"]


@pytest.fixture(scope="session")
def test_db():
    db = get_db(TEST_DB)
    try:
        db.command("ping")
    except Exception as exc:  # pragma: no cover - environment dependent
        pytest.skip(f"MongoDB not reachable: {exc}")
    from app.seed.seed import seed

    seed(db, reset_history=True)
    yield db
    for name in db.list_collection_names():
        db[name].delete_many({})


@pytest.fixture(scope="session")
def repo(test_db) -> Repository:
    return Repository(test_db)
