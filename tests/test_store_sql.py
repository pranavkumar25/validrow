"""SqlJobStore round-trip against aiosqlite (proves the durable backend)."""
from __future__ import annotations

from eve.jobs.models import ColumnMapping, Job, JobStatus
from eve.jobs.store import SqlJobStore


async def test_sql_job_store_roundtrip(tmp_path):
    url = f"sqlite+aiosqlite:///{tmp_path/'jobs.db'}"
    store = SqlJobStore(url)
    await store.init()

    job = Job(
        file_key="k1",
        filename="list.csv",
        mapping=ColumnMapping(email="email", first_name="fn"),
    )
    await store.create(job)

    got = await store.get(job.id)
    assert got is not None
    assert got.file_key == "k1"
    assert got.mapping.email == "email"
    assert got.mapping.first_name == "fn"
    assert got.status is JobStatus.PENDING

    got.status = JobStatus.COMPLETED
    got.counts.valid = 5
    got.counts.total_rows = 10
    got.output_keys = {"cleaned": "out_cleaned.csv"}
    await store.save(got)

    again = await store.get(job.id)
    assert again.status is JobStatus.COMPLETED
    assert again.counts.valid == 5
    assert again.counts.total_rows == 10
    assert again.output_keys["cleaned"] == "out_cleaned.csv"

    all_jobs = await store.list()
    assert len(all_jobs) == 1
