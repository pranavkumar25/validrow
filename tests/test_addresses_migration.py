"""An existing workspace database must survive a schema change.

``create_all`` only creates missing tables, so a database written by an earlier
version keeps its old columns. These build a table in the pre-``smtp_ran`` shape
and assert that opening it both upgrades the schema and corrects the rows whose
settling layer was computed under the old rule.
"""
from __future__ import annotations

import json

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from eve.addresses import AddressStore

OLD_SCHEMA = """
CREATE TABLE addresses (
    email VARCHAR(320) NOT NULL PRIMARY KEY,
    domain VARCHAR(255),
    status VARCHAR(32),
    sub_status VARCHAR(64),
    score INTEGER,
    job_id VARCHAR(64),
    job_filename VARCHAR(512),
    list_type VARCHAR(64),
    checked_at FLOAT,
    checked_day VARCHAR(10),
    mx_found BOOLEAN,
    is_catch_all BOOLEAN,
    is_disposable BOOLEAN,
    is_role BOOLEAN,
    is_free BOOLEAN,
    settled_at INTEGER,
    checks JSON
)
"""


async def _write_old_database(url: str) -> None:
    engine = create_async_engine(url)
    async with engine.begin() as conn:
        await conn.execute(text(OLD_SCHEMA))
        await conn.execute(
            text(
                "INSERT INTO addresses (email, domain, status, sub_status, score,"
                " job_id, job_filename, list_type, checked_at, checked_day,"
                " mx_found, is_catch_all, is_disposable, is_role, is_free,"
                " settled_at, checks)"
                " VALUES (:email, :domain, :status, :sub, :score, 'j1', 'old.csv',"
                " 'Imports', 1785000000.0, '2026-07-24', 0, 0, 0, 0, 0,"
                " :settled, :checks)"
            ),
            [
                # A DNS timeout, written when the rule wrongly credited layer 6.
                {
                    "email": "slow@unreachable.test",
                    "domain": "unreachable.test",
                    "status": "unknown",
                    "sub": "timeout",
                    "score": 40,
                    "settled": 6,
                    "checks": json.dumps(
                        {"syntax": {"valid": True}, "dns_mx": {"mx_found": False,
                                                               "error": "timeout"}}
                    ),
                },
                # A genuine layer-6 result, which must be left alone.
                {
                    "email": "real@acme.io",
                    "domain": "acme.io",
                    "status": "valid",
                    "sub": "ok",
                    "score": 95,
                    "settled": 6,
                    "checks": json.dumps(
                        {"smtp": {"outcome": "valid", "code": 250, "detail": ""}}
                    ),
                },
            ],
        )
    await engine.dispose()


@pytest.fixture
async def upgraded(tmp_path):
    url = f"sqlite+aiosqlite:///{tmp_path}/old.db"
    await _write_old_database(url)
    store = AddressStore(url)
    await store.init()  # runs the migration
    return store


async def test_migration_adds_the_new_column_without_losing_rows(upgraded):
    rows, total = await upgraded.query()
    assert total == 2
    assert {r["email"] for r in rows} == {"slow@unreachable.test", "real@acme.io"}


async def test_migration_backfills_whether_the_probe_ran(upgraded):
    rows = {r["email"]: r for r in (await upgraded.query())[0]}
    assert rows["real@acme.io"]["smtp_ran"] is True
    assert rows["slow@unreachable.test"]["smtp_ran"] is False


async def test_migration_corrects_a_timeout_wrongly_credited_to_layer_six(upgraded):
    """The probe never ran, so the timeout was DNS's — layer 4, not 6."""
    rows = {r["email"]: r for r in (await upgraded.query())[0]}
    assert rows["slow@unreachable.test"]["settled_at"] == 4
    assert rows["real@acme.io"]["settled_at"] == 6

    assert await upgraded.settled_breakdown() == {4: 1, 6: 1}


async def test_migration_is_idempotent(upgraded):
    await upgraded.init()
    await upgraded.init()
    _, total = await upgraded.query()
    assert total == 2
    assert await upgraded.settled_breakdown() == {4: 1, 6: 1}


async def test_writes_still_work_after_migration(upgraded):
    from eve.addresses import AddressRecord

    await upgraded.upsert_many([
        AddressRecord("new@x.com", "x.com", "risky", "catch_all", 50, "j2", "new.csv",
                      checked_at=1785100000.0, smtp_ran=True, settled_at=7)
    ])
    _, total = await upgraded.query()
    assert total == 3
    assert (await upgraded.settled_breakdown())[7] == 1
