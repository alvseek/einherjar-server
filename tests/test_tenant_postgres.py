"""A tenant is created once per (iss, sub) and reused afterwards, against a real Postgres.

Skipped unless ``EINHERJAR_PG_TEST_URL`` names a database carrying Munnin's ``user_identity`` and
``account`` tables. The service reads those tables and never creates them, so a database without
them is not the shape this resolver runs against.
"""

from __future__ import annotations

import os
import uuid

import psycopg
import pytest
from psycopg.rows import tuple_row

from einherjar.business.tenant import TenantResolver

PG = os.getenv("EINHERJAR_PG_TEST_URL")
pytestmark = pytest.mark.skipif(
    not PG, reason="no EINHERJAR_PG_TEST_URL: the tenant resolver needs a real Postgres"
)


@pytest.fixture()
def resolver() -> TenantResolver:
    assert PG is not None
    with psycopg.connect(PG, row_factory=tuple_row) as conn, conn.cursor() as cur:
        cur.execute("SELECT to_regclass('user_identity'), to_regclass('account')")
        identity, account = cur.fetchone()
    if identity is None or account is None:
        pytest.skip("the database does not carry Munnin's user_identity/account tables")
    return TenantResolver(db_url=PG)


def test_resolve_creates_one_tenant_and_reuses_it(resolver: TenantResolver) -> None:
    sub = f"test-{uuid.uuid4().hex}"
    first = resolver.resolve("https://auth.example/oidc", sub, email="a@example.test")
    second = resolver.resolve("https://auth.example/oidc", sub, email="a@example.test")

    assert first == second
    assert resolver.resolve_existing("https://auth.example/oidc", sub) == first


def test_resolve_existing_refuses_an_unmapped_pair(resolver: TenantResolver) -> None:
    with pytest.raises(LookupError):
        resolver.resolve_existing("https://auth.example/oidc", f"missing-{uuid.uuid4().hex}")
