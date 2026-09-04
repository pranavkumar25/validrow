"""Workspace isolation.

The address table de-duplicates by address, so the interesting case is two
workspaces validating the *same* mailbox: each must keep its own verdict rather
than the later write clobbering the earlier one.
"""
from __future__ import annotations

import pytest

from eve.addresses import AddressRecord, AddressStore
from eve.config import Settings, set_settings
from eve.jobs.models import Job
from eve.jobs.store import InMemoryJobStore, SqlJobStore
from eve.tenancy import current_workspace_id, set_current_workspace_id


def _record(email: str, *, job_id: str, score: int = 90) -> AddressRecord:
    return AddressRecord(
        email=email,
        domain=email.split("@")[1],
        status="valid",
        sub_status="",
        score=score,
        job_id=job_id,
        job_filename="list.csv",
        checked_at=1_700_000_000.0,
    )


@pytest.fixture
async def store(tmp_path):
    s = AddressStore(f"sqlite+aiosqlite:///{tmp_path}/ws.db")
    await s.init()
    return s


async def test_same_address_in_two_workspaces_keeps_both_verdicts(store):
    set_current_workspace_id("tenant-a")
    await store.upsert_many([_record("john@acme.com", job_id="job-a", score=91)])

    set_current_workspace_id("tenant-b")
    await store.upsert_many([_record("john@acme.com", job_id="job-b", score=42)])

    set_current_workspace_id("tenant-a")
    mine = await store.get("john@acme.com")
    assert mine["job_id"] == "job-a"
    assert mine["score"] == 91

    set_current_workspace_id("tenant-b")
    theirs = await store.get("john@acme.com")
    assert theirs["job_id"] == "job-b"
    assert theirs["score"] == 42


async def test_re_running_a_list_still_refreshes_in_place(store):
    set_current_workspace_id("tenant-a")
    await store.upsert_many([_record("john@acme.com", job_id="job-1", score=50)])
    await store.upsert_many([_record("john@acme.com", job_id="job-2", score=95)])

    assert (await store.headline())["total"] == 1
    assert (await store.get("john@acme.com"))["score"] == 95


async def test_aggregates_do_not_count_another_workspace(store):
    set_current_workspace_id("tenant-a")
    await store.upsert_many(
        [_record("a@acme.com", job_id="j"), _record("b@acme.com", job_id="j")]
    )
    set_current_workspace_id("tenant-b")
    await store.upsert_many([_record("c@other.com", job_id="j")])

    set_current_workspace_id("tenant-a")
    assert (await store.headline())["total"] == 2
    assert sum((await store.totals()).values()) == 2
    assert sum(sum(d.values()) for d in (await store.by_day()).values()) == 2
    assert sum(x["count"] for x in await store.top_domains()) == 2
    assert sum((await store.settled_breakdown()).values()) == 2
    _, total = await store.query()
    assert total == 2
    assert len(await store.stream()) == 2

    set_current_workspace_id("tenant-b")
    assert (await store.headline())["total"] == 1


async def test_delete_by_job_stops_at_the_workspace_boundary(store):
    set_current_workspace_id("tenant-a")
    await store.upsert_many([_record("a@acme.com", job_id="shared-id")])
    set_current_workspace_id("tenant-b")
    await store.upsert_many([_record("b@acme.com", job_id="shared-id")])

    set_current_workspace_id("tenant-a")
    assert await store.delete_by_job("shared-id") == 1

    set_current_workspace_id("tenant-b")
    assert (await store.headline())["total"] == 1


async def test_job_carries_the_current_workspace():
    set_settings(Settings(workspace_id="acme"))
    assert Job(file_key="k", filename="f.csv").workspace_id == "acme"

    set_current_workspace_id("override")
    assert Job(file_key="k", filename="f.csv").workspace_id == "override"
    assert current_workspace_id() == "override"


@pytest.mark.parametrize("sql", [False, True])
async def test_job_store_hides_other_workspaces(tmp_path, sql):
    store = (
        SqlJobStore(f"sqlite+aiosqlite:///{tmp_path}/jobs.db")
        if sql
        else InMemoryJobStore()
    )
    await store.init()

    set_current_workspace_id("tenant-a")
    mine = await store.create(Job(file_key="k", filename="a.csv"))
    set_current_workspace_id("tenant-b")
    theirs = await store.create(Job(file_key="k", filename="b.csv"))

    # Each workspace numbers its own runs from #1.
    assert mine.seq == 1 and theirs.seq == 1

    set_current_workspace_id("tenant-a")
    assert [j.filename for j in await store.list()] == ["a.csv"]
    # Another workspace's id reads as missing rather than forbidden — a 403
    # would confirm the job exists.
    assert await store.get(theirs.id) is None
    assert await store.delete(theirs.id) is False

    set_current_workspace_id("tenant-b")
    assert (await store.get(theirs.id)).filename == "b.csv"
