"""End-to-end pipeline test (offline: DNS + SMTP disabled)."""
from __future__ import annotations

import io

from conftest import parse_csv
from eve.config import Settings
from eve.jobs.models import ColumnMapping, Job
from eve.jobs.pipeline import run_job
from eve.jobs.store import get_job_store
from eve.storage import get_object_store

CSV = b"""email,first_name,last_name,company
john.doe@gmail.com,john,doe,Acme
johndoe+promo@gmail.com,john,doe,Acme
sales@acme.io,,,Acme
x@mailinator.com,foo,bar,Spam
not-an-email,bad,row,X
jane@company.io,jane,smith,Company
"""


async def _load_output(store, job, segment):
    fh = await store.open_read(job.output_keys[segment])
    try:
        return parse_csv(fh.read())
    finally:
        fh.close()


async def test_pipeline_end_to_end():
    store = get_object_store()
    job_store = get_job_store()
    await store.put("input.csv", io.BytesIO(CSV))

    job = Job(
        file_key="input.csv",
        filename="list.csv",
        mapping=ColumnMapping(email="email", first_name="first_name", last_name="last_name"),
    )
    await job_store.create(job)

    settings = Settings(enable_dns=False, enable_smtp=False, chunk_size=100, verify_concurrency=8)
    await run_job(job, store=store, job_store=job_store, settings=settings)

    assert job.status.value == "completed"
    c = job.counts
    assert c.total_rows == 6
    assert c.unique_emails == 5
    assert c.duplicates == 1
    assert c.disposable == 1
    assert c.invalid == 1
    assert c.risky == 1        # role account
    assert c.unknown == 2      # good addresses, mailbox unprobed offline

    cleaned = await _load_output(store, job, "cleaned")
    valid = await _load_output(store, job, "valid")
    removed = await _load_output(store, job, "removed")

    assert len(cleaned) == 6
    assert len(valid) == 3     # 2 unknown + 1 risky, minus the duplicate
    assert len(removed) == 3   # duplicate + invalid + disposable
    assert len(valid) + len(removed) == len(cleaned)

    # Original columns preserved + verdict columns appended.
    assert set(["email", "first_name", "last_name", "company"]).issubset(cleaned[0].keys())
    assert "email_status" in cleaned[0]

    # Name hygiene: title-cased.
    john = next(r for r in cleaned if r["email"] == "john.doe@gmail.com")
    assert john["first_name"] == "John" and john["last_name"] == "Doe"

    # Duplicate carries duplicate_of pointing at the canonical address.
    dup = next(r for r in cleaned if r["email"] == "johndoe+promo@gmail.com")
    assert dup["duplicate_of"] == "john.doe@gmail.com"


async def test_pipeline_smtp_path_with_fake_prober():
    """With SMTP enabled + a fake prober, verdicts reflect mailbox existence."""
    from eve.layers.dns_mx import MxResult, _cache
    from eve.layers.smtp import ProbeResult

    store = get_object_store()
    job_store = get_job_store()
    csv_bytes = b"email\ngood@corp.io\nbad@corp.io\nany@catchall.io\n"
    await store.put("in.csv", io.BytesIO(csv_bytes))

    # Pre-warm the DNS cache so mx_found=True without a network call.
    for d in ("corp.io", "catchall.io"):
        _cache.put(d, MxResult(d, True, [f"mx.{d}"]), ttl=60)

    class FakeProber:
        async def probe(self, email, mx_hosts, domain):
            if domain == "catchall.io":
                return ProbeResult(outcome="catch_all", is_catch_all=True, smtp_code=250)
            if email.startswith("good@"):
                return ProbeResult(outcome="valid", smtp_code=250)
            return ProbeResult(outcome="invalid", smtp_code=550)

    job = Job(file_key="in.csv", filename="in.csv", mapping=ColumnMapping(email="email"))
    await job_store.create(job)
    settings = Settings(enable_dns=True, enable_smtp=True)
    await run_job(job, store=store, job_store=job_store, prober=FakeProber(), settings=settings)

    assert job.counts.valid == 1       # good@corp.io
    assert job.counts.invalid == 1     # bad@corp.io
    assert job.counts.risky == 1       # catch-all domain


async def test_pipeline_smtp_demo_mode_dns_off():
    """SMTP demo mode: DNS off, the prober's target decides — still yields
    valid/invalid (this is how the local mock-mailserver demo runs)."""
    from eve.layers.smtp import ProbeResult

    store = get_object_store()
    job_store = get_job_store()
    await store.put("demo.csv", io.BytesIO(b"email\nalice@acme-demo.com\nghost@acme-demo.com\n"))

    class FakeProber:
        async def probe(self, email, mx_hosts, domain):
            if email.startswith("alice@"):
                return ProbeResult(outcome="valid", smtp_code=250)
            return ProbeResult(outcome="invalid", smtp_code=550)

    job = Job(file_key="demo.csv", filename="demo.csv", mapping=ColumnMapping(email="email"))
    await job_store.create(job)
    settings = Settings(enable_dns=False, enable_smtp=True)
    await run_job(job, store=store, job_store=job_store, prober=FakeProber(), settings=settings)

    assert job.counts.valid == 1
    assert job.counts.invalid == 1


async def test_csv_writer_spills_instead_of_holding_the_output(tmp_path):
    """Output memory must not scale with rows — the assembly pass is a stream."""
    from eve.jobs.csv_io import CsvWriter

    store = get_object_store()
    w = CsvWriter(store, "spilled.csv", ["a", "b"], spill_bytes=1024)
    for i in range(5000):
        w.write({"a": f"value-{i}", "b": "x" * 50})

    assert w._sink is not None, "never spilled: the whole file was held in memory"
    assert w._buf.tell() < 1024, "buffer kept growing past the spill threshold"

    await w.close()
    fh = await store.open_read("spilled.csv")
    try:
        rows = parse_csv(fh.read())
    finally:
        fh.close()

    assert len(rows) == 5000
    assert rows[0] == {"a": "value-0", "b": "x" * 50}
    assert rows[-1] == {"a": "value-4999", "b": "x" * 50}


async def test_a_small_csv_never_touches_the_disk():
    from eve.jobs.csv_io import CsvWriter

    store = get_object_store()
    w = CsvWriter(store, "small.csv", ["a"])
    w.write({"a": "1"})
    assert w._sink is None
    await w.close()

    fh = await store.open_read("small.csv")
    try:
        assert parse_csv(fh.read()) == [{"a": "1"}]
    finally:
        fh.close()
