"""Session-scoped DB fixture: connect once, apply the generated model DDL once.

Model tests are integration tests by design (specs/slice-2.md, Design): the
physics is one SQL function, so the seam is that function through psycopg, not
a Python callable.
"""
from __future__ import annotations

import os

import psycopg
import pytest

from models import apply_models

DSN = os.environ.get(
    "DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/resource"
)


@pytest.fixture(scope="session")
def db():
    conn = psycopg.connect(DSN, autocommit=True)
    with conn.cursor() as cur:
        apply_models(cur)
    yield conn
    conn.close()


@pytest.fixture
def cur(db):
    with db.cursor() as cur:
        yield cur
