"""The workspace read-model: de-duplication, filtering and rollups."""
from __future__ import annotations

import pytest

from eve.addresses import AddressRecord, AddressStore
from eve.verdict import Status, SubStatus, Verdict


@pytest.fixture
async def store(tmp_path):
    s = AddressStore(f"sqlite+aiosqlite:///{tmp_path}/ws.db")
    await s.init()
    return s


def _rec(email, status="valid", sub="ok", score=90, domain=None, job="j1", ts=1000.0, **kw):
    return AddressRecord(
        email=email,
        domain=domain or email.split("@")[1],
        status=status,
        sub_status=sub,
        score=score,
        job_id=job,
        job_filename=f"{job}.csv",
        checked_at=ts,
        **kw,
    )


async def test_addresses_are_deduplicated_by_address(store):
    """Re-running a list refreshes the row rather than double-counting it."""
    await store.upsert_many([_rec("a@x.com", score=50, ts=1000.0)])
    await store.upsert_many([_rec("a@x.com", score=90, ts=2000.0, job="j2")])

    rows, total = await store.query()
    assert total == 1
    assert rows[0]["score"] == 90
    assert rows[0]["job_id"] == "j2"  # belongs to the run that produced it last


async def test_query_filters_by_primary_verdict_across_six_statuses(store):
    await store.upsert_many([
        _rec("ok@x.com", "valid", "ok"),
        _rec("bad@x.com", "invalid", "mailbox_not_found"),
        _rec("temp@x.com", "disposable", "disposable"),
        _rec("role@x.com", "risky", "role_account"),
    ])
    # `disposable` and `invalid` both fold into the undeliverable verdict.
    _, n = await store.query(verdicts=["undeliverable"])
    assert n == 2
    _, n = await store.query(verdicts=["deliverable"])
    assert n == 1


async def test_search_matches_local_part_and_domain(store):
    await store.upsert_many([_rec("jane@acme.io"), _rec("bob@other.com")])
    _, n = await store.query(search="acme")
    assert n == 1
    _, n = await store.query(search="jane")
    assert n == 1
    _, n = await store.query(search="nothing")
    assert n == 0


async def test_sorting_and_paging(store):
    await store.upsert_many([
        _rec("a@x.com", score=10, ts=3000.0),
        _rec("b@x.com", score=90, ts=1000.0),
        _rec("c@x.com", score=50, ts=2000.0),
    ])
    rows, _ = await store.query(sort="scoreDown")
    assert [r["score"] for r in rows] == [90, 50, 10]
    rows, _ = await store.query(sort="az")
    assert [r["email"] for r in rows] == ["a@x.com", "b@x.com", "c@x.com"]
    rows, total = await store.query(sort="recent", limit=2)
    assert total == 3 and len(rows) == 2 and rows[0]["email"] == "a@x.com"


async def test_totals_headline_and_top_domains(store):
    await store.upsert_many([
        _rec("a@acme.io", "valid", score=90),
        _rec("b@acme.io", "risky", "role_account", score=50),
        _rec("c@other.com", "invalid", "no_mx", score=3),
    ])
    totals = await store.totals()
    assert totals == {"deliverable": 1, "risky": 1, "unknown": 0, "undeliverable": 1}

    head = await store.headline()
    assert head["total"] == 3
    assert head["domains"] == 2
    assert head["avg_score"] == 48  # (90+50+3)/3

    tops = await store.top_domains()
    assert tops[0]["domain"] == "acme.io" and tops[0]["count"] == 2


async def test_by_day_groups_for_the_charts(store):
    await store.upsert_many([
        _rec("a@x.com", "valid", ts=1785000000.0),
        _rec("b@x.com", "risky", "role_account", ts=1785000000.0),
    ])
    by_day = await store.by_day()
    day = next(iter(by_day))
    assert by_day[day]["deliverable"] == 1
    assert by_day[day]["risky"] == 1


async def test_empty_store_reports_empty_rather_than_erroring(store):
    assert await store.is_empty() is True
    assert await store.totals() == {k: 0 for k in
                                    ("deliverable", "risky", "unknown", "undeliverable")}
    head = await store.headline()
    assert head == {"total": 0, "domains": 0, "avg_score": None}
    assert await store.top_domains() == []
    rows, total = await store.query()
    assert rows == [] and total == 0


async def test_delete_by_job_removes_only_that_run(store):
    await store.upsert_many([_rec("a@x.com", job="j1"), _rec("b@x.com", job="j2")])
    assert await store.delete_by_job("j1") == 1
    rows, total = await store.query()
    assert total == 1 and rows[0]["job_id"] == "j2"


async def test_record_from_verdict_captures_the_settling_layer(store):
    v = Verdict(
        email="x@tempbox.io",
        normalized_email="x@tempbox.io",
        domain="tempbox.io",
        status=Status.DISPOSABLE,
        sub_status=SubStatus.DISPOSABLE,
        score=15,
        is_disposable=True,
        checks={"classify": {"disposable": True}},
    )
    rec = AddressRecord.from_verdict(v, job_id="j1", job_filename="j1.csv")
    assert rec.settled_at == 5
    assert rec.is_disposable is True

    await store.upsert_many([rec])
    assert await store.settled_breakdown() == {5: 1}
    assert await store.sub_reason_count("disposable") == 1
