"""Shared fixtures.

Integration tests run against the Postgres that `make up` provides, using the
data already loaded into it. They assert invariants over whatever is loaded
rather than seeding their own fixtures: the point of this suite is to catch a
normalizer or loader regression against the real published files, and a
hand-built fixture would only re-test the assumptions that produced it.

Tests that need loaded data skip cleanly when the database is empty, so a fresh
clone can run `make test` before `make load` without a wall of failures.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.exc import OperationalError

from app.db import SessionLocal
from app.main import app
from app.models import EmployerLMIA


@pytest.fixture(scope="session")
def session():
    try:
        with SessionLocal() as s:
            s.execute(select(1))
            yield s
    except OperationalError as exc:
        pytest.skip(f"Postgres is not reachable — run `make up` first ({exc.orig})")


@pytest.fixture(scope="session")
def loaded(session) -> int:
    rows = session.scalar(select(func.count()).select_from(EmployerLMIA)) or 0
    if rows == 0:
        pytest.skip("no data loaded — run `make load` first")
    return rows


@pytest.fixture(scope="session")
def client(loaded) -> TestClient:
    return TestClient(app)
