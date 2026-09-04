"""Deferred re-probes: the schedule, the sweep, and what a cleared retry updates.

Nothing here opens a socket. The prober is a stub whose reply per address is
scripted, which is what makes "greylisted twice, then accepted" a test rather
than a wait.
"""
from __future__ import annotations

import asyncio

import pytest

import eve.reprobe as reprobe
import eve.smtp_infra as smtp_infra
from eve.addresses import AddressRecord, AddressStore, set_address_store
from eve.config import Settings
from eve.layers.smtp import ProbeResult
from eve.reprobe import (
    ReprobeStore,
    is_greylisted,
    policy_from_settings,
    run_due_reprobes,
    set_reprobe_store,
    start_reprobe_runner,
    stop_reprobe_runner,
)
from eve.smtp_infra.greylist import RetryPolicy
from eve.verdict import Status, SubStatus

WS = "acme"


class ScriptedProber:
    """Returns the next scripted ProbeResult per address; repeats the last one."""

    def __init__(self, script: dict[str, list[ProbeResult]]):
        self.script = {k: list(v) for k, v in script.items()}
        self.calls: list[str] = []

    async def probe(self, email: str, mx_hosts: list[str], domain: str) -> ProbeResult:
        self.calls.append(email)
        queue = self.script.get(email) or [ProbeResult(outcome="unknown", detail="unknown")]
        return queue.pop(0) if len(queue) > 1 else queue[0]


def _greylisted(code: int = 451) -> ProbeResult:
    return ProbeResult(outcome="unknown", smtp_code=code, detail="greylisted")


def _accepted() -> ProbeResult:
    return ProbeResult(outcome="valid", smtp_code=250, detail="250 accepted")


def _rejected() -> ProbeResult:
    return ProbeResult(outcome="invalid", smtp_code=550, detail="550 no such user")


def _settings(**over) -> Settings:
    base = {
        "enable_smtp": True,
        "enable_dns": False,  # no resolver in tests; the prober decides everything
        "smtp_target_host": "127.0.0.1",
        "reprobe_delays": "900,1800,3600",
        "reprobe_max_attempts": 3,
    }
    base.update(over)
    return Settings(**base)


@pytest.fixture
async def stores(tmp_path):
    """A real SQLite workspace: both stores share one database, as in production."""
    url = f"sqlite+aiosqlite:///{tmp_path}/ws.db"
    addresses = AddressStore(url)
    await addresses.init()  # migrates to head, which creates `reprobes` too
    reprobes = ReprobeStore(url)
    set_address_store(addresses)
    set_reprobe_store(reprobes)
    try:
        yield addresses, reprobes
    finally:
        await stop_reprobe_runner()
        set_address_store(None)
        set_reprobe_store(None)
        smtp_infra.set_async_prober(None)


async def _seed_address(store: AddressStore, email: str, **kw) -> None:
    await store.upsert_many(
        [
            AddressRecord(
                email=email,
                domain=email.split("@")[1],
                status=kw.pop("status", "unknown"),
                sub_status=kw.pop("sub_status", "greylisted"),
                score=kw.pop("score", 60),
                job_id="j1",
                job_filename="list.csv",
                list_type="cold",
                checked_at=1000.0,
                smtp_ran=True,
                **kw,
            )
        ],
        workspace_id=WS,
    )


# --- detection and policy ------------------------------------------------


def test_greylisting_is_read_from_the_smtp_check():
    assert is_greylisted({"smtp": {"outcome": "unknown", "detail": "greylisted"}})
    assert not is_greylisted({"smtp": {"outcome": "unknown", "detail": "antispam_block"}})
    assert not is_greylisted({"smtp": {"outcome": "valid", "detail": "250 accepted"}})
    assert not is_greylisted({})
    assert not is_greylisted(None)


def test_policy_comes_from_settings():
    pol = policy_from_settings(_settings(reprobe_delays="60, 120 ,240", reprobe_max_attempts=2))
    assert pol.max_attempts == 2
    assert pol.delays == [60, 120, 240]
    assert pol.next_delay(0) == 60
    assert pol.next_delay(1) == 120
    assert pol.next_delay(2) == -1  # attempts exhausted


def test_malformed_delays_are_skipped_not_fatal():
    assert _settings(reprobe_delays="900,,abc, 1800 ").reprobe_delay_list == [900, 1800]


# --- the schedule --------------------------------------------------------


async def test_schedule_places_the_retry_in_the_future(stores):
    _, reprobes = stores
    due = await reprobes.schedule("a@x.com", workspace_id=WS, job_id="j1", now=1000.0)

    assert due == 1900.0  # first delay is 900s
    rows = await reprobes.pending(workspace_id=WS)
    assert len(rows) == 1
    assert rows[0]["email"] == "a@x.com"
    assert rows[0]["attempt"] == 0


async def test_schedule_returns_none_once_attempts_are_exhausted(stores):
    _, reprobes = stores
    pol = RetryPolicy(max_attempts=2, delays=[10, 20])
    assert await reprobes.schedule("a@x.com", workspace_id=WS, attempt=1, policy=pol) is not None
    assert await reprobes.schedule("a@x.com", workspace_id=WS, attempt=2, policy=pol) is None


async def test_retries_are_scoped_to_a_workspace(stores):
    _, reprobes = stores
    await reprobes.schedule("a@x.com", workspace_id="one", now=1000.0)
    await reprobes.schedule("a@x.com", workspace_id="two", now=1000.0)

    assert len(await reprobes.pending(workspace_id="one")) == 1
    assert len(await reprobes.pending(workspace_id="two")) == 1


async def test_only_due_rows_are_claimed(stores):
    _, reprobes = stores
    await reprobes.schedule("soon@x.com", workspace_id=WS, now=0.0)  # due at 900
    await reprobes.schedule("later@x.com", workspace_id=WS, now=1000.0)  # due at 1900

    claimed = await reprobes.claim_due(now=1000.0)
    assert [r["email"] for r in claimed] == ["soon@x.com"]


async def test_claiming_leases_a_row_against_a_second_poller(stores):
    """Two processes sweeping at once must not re-probe the same address twice."""
    _, reprobes = stores
    await reprobes.schedule("a@x.com", workspace_id=WS, now=0.0)

    first = await reprobes.claim_due(now=1000.0, lease_seconds=300.0)
    second = await reprobes.claim_due(now=1000.0)

    assert len(first) == 1
    assert second == [], "a leased row was handed to a second poller"
    # The lease expires on its own — a poller that dies releases its work.
    assert len(await reprobes.claim_due(now=1400.0)) == 1


# --- the sweep -----------------------------------------------------------


async def test_a_cleared_greylist_updates_the_address(stores):
    addresses, reprobes = stores
    await _seed_address(addresses, "a@x.com")
    await reprobes.schedule("a@x.com", workspace_id=WS, job_id="j1", now=0.0)
    smtp_infra.set_async_prober(ScriptedProber({"a@x.com": [_accepted()]}))

    tally = await run_due_reprobes(settings=_settings())

    assert tally["resolved"] == 1
    row = await addresses.get("a@x.com", workspace_id=WS)
    assert row["status"] == Status.VALID.value
    assert row["sub_status"] == SubStatus.OK.value
    # Settled, so it is off the retry list.
    assert await reprobes.pending_count(workspace_id=WS) == 0


async def test_a_retry_keeps_the_address_attributed_to_its_original_job(stores):
    """The retry is a later reading of the same address, not a new run."""
    addresses, reprobes = stores
    await _seed_address(addresses, "a@x.com")
    await reprobes.schedule("a@x.com", workspace_id=WS, job_id="j1", now=0.0)
    smtp_infra.set_async_prober(ScriptedProber({"a@x.com": [_accepted()]}))

    await run_due_reprobes(settings=_settings())

    row = await addresses.get("a@x.com", workspace_id=WS)
    assert row["job_id"] == "j1"
    assert row["job_filename"] == "list.csv"
    assert row["list_type"] == "cold"


async def test_a_retry_can_also_prove_the_mailbox_missing(stores):
    addresses, reprobes = stores
    await _seed_address(addresses, "ghost@x.com")
    await reprobes.schedule("ghost@x.com", workspace_id=WS, job_id="j1", now=0.0)
    smtp_infra.set_async_prober(ScriptedProber({"ghost@x.com": [_rejected()]}))

    assert (await run_due_reprobes(settings=_settings()))["resolved"] == 1
    row = await addresses.get("ghost@x.com", workspace_id=WS)
    assert row["status"] == Status.INVALID.value


async def test_still_greylisted_reschedules_with_the_next_delay(stores):
    _, reprobes = stores
    await reprobes.schedule("a@x.com", workspace_id=WS, job_id="j1", now=0.0)
    smtp_infra.set_async_prober(ScriptedProber({"a@x.com": [_greylisted()]}))

    assert (await run_due_reprobes(settings=_settings()))["retried"] == 1

    rows = await reprobes.pending(workspace_id=WS)
    assert len(rows) == 1
    assert rows[0]["attempt"] == 1  # one retry made, still waiting
    assert rows[0]["last_code"] == 451


async def test_exhausted_retries_settle_unknown_and_leave_the_queue(stores):
    """Bounded retries, and the final state is the honest unknown it started as."""
    addresses, reprobes = stores
    await _seed_address(addresses, "a@x.com")
    prober = ScriptedProber({"a@x.com": [_greylisted()]})
    smtp_infra.set_async_prober(prober)
    s = _settings(reprobe_max_attempts=3)

    await reprobes.schedule("a@x.com", workspace_id=WS, job_id="j1", now=0.0)
    outcomes = []
    for _ in range(3):
        # Force the row due each sweep rather than waiting out real delays.
        await reprobes.schedule(
            "a@x.com",
            workspace_id=WS,
            job_id="j1",
            attempt=(await reprobes.pending(workspace_id=WS))[0]["attempt"],
            now=-10_000.0,
        )
        outcomes.append(await run_due_reprobes(settings=s))
        if not await reprobes.pending(workspace_id=WS):
            break

    assert outcomes[-1]["exhausted"] == 1
    assert await reprobes.pending_count(workspace_id=WS) == 0
    row = await addresses.get("a@x.com", workspace_id=WS)
    assert row["status"] == Status.UNKNOWN.value
    assert row["sub_status"] == SubStatus.GREYLISTED.value


async def test_one_failing_address_does_not_sink_the_batch(stores):
    addresses, reprobes = stores
    await _seed_address(addresses, "good@x.com")
    await _seed_address(addresses, "boom@x.com")
    await reprobes.schedule("good@x.com", workspace_id=WS, job_id="j1", now=0.0)
    await reprobes.schedule("boom@x.com", workspace_id=WS, job_id="j1", now=0.0)

    class HalfBroken(ScriptedProber):
        async def probe(self, email, mx_hosts, domain):
            if email == "boom@x.com":
                raise RuntimeError("connection reset")
            return await super().probe(email, mx_hosts, domain)

    smtp_infra.set_async_prober(HalfBroken({"good@x.com": [_accepted()]}))

    tally = await run_due_reprobes(settings=_settings())

    assert tally["claimed"] == 2
    assert tally["resolved"] == 1
    assert tally["failed"] == 1
    assert (await addresses.get("good@x.com", workspace_id=WS))["status"] == Status.VALID.value


async def test_an_empty_queue_is_a_cheap_no_op(stores):
    assert await run_due_reprobes(settings=_settings()) == {
        "claimed": 0,
        "resolved": 0,
        "retried": 0,
        "exhausted": 0,
        "failed": 0,
    }


# --- the poller ----------------------------------------------------------


async def test_the_runner_sweeps_due_work(stores):
    addresses, reprobes = stores
    await _seed_address(addresses, "a@x.com")
    await reprobes.schedule("a@x.com", workspace_id=WS, job_id="j1", now=-10_000.0)
    smtp_infra.set_async_prober(ScriptedProber({"a@x.com": [_accepted()]}))

    task = start_reprobe_runner(_settings(reprobe_poll_seconds=0.01))
    assert task is not None
    for _ in range(200):
        if (await addresses.get("a@x.com", workspace_id=WS))["status"] == Status.VALID.value:
            break
        await asyncio.sleep(0.01)
    await stop_reprobe_runner()

    assert (await addresses.get("a@x.com", workspace_id=WS))["status"] == Status.VALID.value


@pytest.mark.parametrize(
    "over, why",
    [
        ({"enable_smtp": False}, "nothing is ever deferred with the probe off"),
        ({"reprobe_enabled": False}, "explicitly turned off"),
    ],
)
async def test_the_runner_stays_off_when_it_could_do_nothing(stores, over, why):
    assert start_reprobe_runner(_settings(**over)) is None, why


async def test_stopping_a_runner_that_never_started_is_safe(stores):
    await stop_reprobe_runner()


async def test_start_is_idempotent(stores):
    s = _settings(reprobe_poll_seconds=30)
    first = start_reprobe_runner(s)
    assert start_reprobe_runner(s) is first
    await stop_reprobe_runner()


# --- deleting a job ------------------------------------------------------


async def test_dropping_a_job_drops_its_pending_retries(stores):
    _, reprobes = stores
    await reprobes.schedule("a@x.com", workspace_id=WS, job_id="j1", now=0.0)
    await reprobes.schedule("b@x.com", workspace_id=WS, job_id="j2", now=0.0)

    assert await reprobes.delete_by_job("j1", workspace_id=WS) == 1
    assert [r["email"] for r in await reprobes.pending(workspace_id=WS)] == ["b@x.com"]


def test_reprobe_module_does_not_import_the_pipeline_at_module_scope():
    """The pipeline imports this module to schedule; this one must not import back."""
    import inspect

    source = inspect.getsource(reprobe)
    module_level = source.split("class ReprobeStore", 1)[0]
    assert "from eve.jobs.pipeline" not in module_level


# --- the pipeline hook ---------------------------------------------------


async def _run_job_with(prober, csv_bytes: bytes, addresses, *, settings=None):
    """Run one bulk job against a scripted prober, DNS off (the demo-mode path)."""
    import io

    from eve.jobs.models import ColumnMapping, Job
    from eve.jobs.pipeline import run_job
    from eve.jobs.store import get_job_store
    from eve.storage import get_object_store

    store = get_object_store()
    job_store = get_job_store()
    await store.put("in.csv", io.BytesIO(csv_bytes))
    job = Job(
        file_key="in.csv",
        filename="in.csv",
        mapping=ColumnMapping(email="email"),
        workspace_id=WS,
    )
    await job_store.create(job)
    await run_job(
        job,
        store=store,
        job_store=job_store,
        prober=prober,
        settings=settings or _settings(),
        addresses=addresses,
    )
    return job


async def test_a_job_queues_its_greylisted_addresses(stores):
    addresses, reprobes = stores
    prober = ScriptedProber(
        {
            "deferred@x.com": [_greylisted(450)],
            "good@x.com": [_accepted()],
            "ghost@x.com": [_rejected()],
        }
    )

    job = await _run_job_with(
        prober, b"email\ndeferred@x.com\ngood@x.com\nghost@x.com\n", addresses
    )

    assert job.status.value == "completed"
    queued = await reprobes.pending(workspace_id=WS)
    assert [r["email"] for r in queued] == ["deferred@x.com"]
    assert queued[0]["job_id"] == job.id
    assert queued[0]["last_code"] == 450


async def test_the_deferred_row_is_unknown_in_the_run_that_deferred_it(stores):
    """The CSVs are a snapshot: a queued retry does not pre-empt this verdict."""
    addresses, _ = stores
    prober = ScriptedProber({"deferred@x.com": [_greylisted()]})

    await _run_job_with(prober, b"email\ndeferred@x.com\n", addresses)

    row = await addresses.get("deferred@x.com", workspace_id=WS)
    assert row["status"] == Status.UNKNOWN.value
    assert row["sub_status"] == SubStatus.GREYLISTED.value


async def test_a_job_with_nothing_deferred_queues_nothing(stores):
    addresses, reprobes = stores
    prober = ScriptedProber({"good@x.com": [_accepted()]})

    await _run_job_with(prober, b"email\ngood@x.com\n", addresses)

    assert await reprobes.pending_count(workspace_id=WS) == 0


async def test_retries_are_not_queued_when_the_feature_is_off(stores):
    addresses, reprobes = stores
    prober = ScriptedProber({"deferred@x.com": [_greylisted()]})

    await _run_job_with(
        prober,
        b"email\ndeferred@x.com\n",
        addresses,
        settings=_settings(reprobe_enabled=False),
    )

    assert await reprobes.pending_count(workspace_id=WS) == 0
