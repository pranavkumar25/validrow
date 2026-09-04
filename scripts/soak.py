"""Soak the bulk pipeline against a synthetic list and report what it cost.

The claim this exists to check is the one in the README: two streaming passes
with O(unique) memory, so a 1M-row file resolves each domain once and never
holds the sheet. A claim like that is either measured or it is decoration.

DNS and SMTP are off. That is the point rather than a limitation — with them on
the number would be a measurement of someone else's resolver and of how fast a
provider answers, which tells you nothing about whether this process holds the
file in memory. What is measured here is the pipeline itself.

    python scripts/soak.py                     # 1,000,000 rows
    python scripts/soak.py --rows 100000
    python scripts/soak.py --rows 1000000 --unique-ratio 0.5 --domains 200000

Peak memory is the process maximum RSS, which includes the interpreter and the
imports, so read the delta between sizes rather than the absolute figure.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import random
import resource
import shutil
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

# Written before importing eve: the engine resolves its storage and workspace
# database from configuration at import-adjacent points, and a soak must not
# touch a real one.
_TMP = Path(tempfile.mkdtemp(prefix="eve-soak-"))
os.environ.setdefault("EVE_LOCAL_STORAGE_DIR", str(_TMP / "storage"))
os.environ.setdefault("EVE_ENABLE_DNS", "false")
os.environ.setdefault("EVE_ENABLE_SMTP", "false")

from eve.config import Settings  # noqa: E402
from eve.jobs.models import ColumnMapping, Job  # noqa: E402
from eve.jobs.pipeline import run_job  # noqa: E402
from eve.jobs.store import InMemoryJobStore  # noqa: E402
from eve.storage import LocalObjectStore  # noqa: E402

# Enough shape to be realistic: a long tail of company domains, a few big free
# providers, some role accounts, some junk. A file of one repeated address would
# make the dedupe look free and the memory claim untestable.
_FREE = ["gmail.com", "yahoo.com", "outlook.com", "hotmail.com"]
_ROLES = ["info", "sales", "support", "admin"]
_FIRST = ["john", "jane", "alex", "sam", "chris", "pat", "robin", "kim", "lee", "morgan"]
_LAST = ["doe", "smith", "jones", "patel", "chen", "garcia", "khan", "brown", "novak", "sato"]


def _rss_mb() -> float:
    """Peak RSS in MB. ru_maxrss is bytes on macOS, kilobytes on Linux."""
    raw = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return raw / (1024 * 1024) if sys.platform == "darwin" else raw / 1024


def _fmt(n: float) -> str:
    return f"{n:,.0f}"


def generate(path: Path, rows: int, unique_ratio: float, domains: int, seed: int = 7) -> dict:
    """Write a CSV of ``rows`` rows and return what actually went into it."""
    rng = random.Random(seed)
    unique_target = max(1, int(rows * unique_ratio))
    pool: list[str] = []
    used_domains = set()

    for i in range(unique_target):
        r = rng.random()
        if r < 0.10:  # free provider
            domain = rng.choice(_FREE)
            local = f"{rng.choice(_FIRST)}.{rng.choice(_LAST)}{i}"
        elif r < 0.16:  # role account on a company domain
            domain = f"company{i % domains}.com"
            local = rng.choice(_ROLES)
        elif r < 0.18:  # junk that fails syntax
            domain = ""
            local = f"not-an-email-{i}"
        else:
            domain = f"company{i % domains}.com"
            local = f"{rng.choice(_FIRST)}.{rng.choice(_LAST)}{i}"
        pool.append(f"{local}@{domain}" if domain else local)
        if domain:
            used_domains.add(domain)

    with path.open("w", encoding="utf-8", newline="") as fh:
        fh.write("email,first_name,last_name,company\n")
        for i in range(rows):
            # Duplicates are drawn from the same pool, which is what a real
            # list looks like: the same person appearing in two exports.
            email = pool[i] if i < len(pool) else pool[rng.randrange(len(pool))]
            fh.write(f"{email},{rng.choice(_FIRST)},{rng.choice(_LAST)},Acme{i % 500}\n")

    return {
        "rows": rows,
        "unique_emails": len(set(pool)),
        "unique_domains": len(used_domains),
        "bytes": path.stat().st_size,
    }


async def soak(rows: int, unique_ratio: float, domains: int, chunk: int, concurrency: int) -> int:
    src = _TMP / "input.csv"

    t0 = time.perf_counter()
    shape = generate(src, rows, unique_ratio, domains)
    gen_s = time.perf_counter() - t0

    print(f"input      {_fmt(shape['rows'])} rows, {shape['bytes'] / 1e6:,.1f} MB")
    print(f"           {_fmt(shape['unique_emails'])} unique emails, "
          f"{_fmt(shape['unique_domains'])} unique domains")
    print(f"           generated in {gen_s:,.1f}s")
    print(f"baseline   peak RSS {_rss_mb():,.0f} MB (interpreter + imports + generator)")

    store = LocalObjectStore(_TMP / "storage")
    job_store = InMemoryJobStore()
    with src.open("rb") as fh:
        await store.put("soak.csv", fh)

    job = Job(
        file_key="soak.csv",
        filename="soak.csv",
        mapping=ColumnMapping(email="email", first_name="first_name", last_name="last_name"),
    )
    await job_store.create(job)

    settings = Settings(
        enable_dns=False,
        enable_smtp=False,
        chunk_size=chunk,
        verify_concurrency=concurrency,
        local_storage_dir=str(_TMP / "storage"),
    )

    t0 = time.perf_counter()
    # addresses=None: the read-model is a separate subsystem with its own
    # database, and including it would measure SQLite rather than the pipeline.
    await run_job(job, store=store, job_store=job_store, settings=settings, addresses=None)
    elapsed = time.perf_counter() - t0

    c = job.counts
    print()
    print(f"status     {job.status.value} in {elapsed:,.1f}s")
    print(f"throughput {_fmt(rows / elapsed)} rows/sec  "
          f"({_fmt(c.unique_emails / elapsed)} unique/sec)")
    print(f"peak RSS   {_rss_mb():,.0f} MB")
    print()
    print(f"counted    {_fmt(c.total_rows)} rows, {_fmt(c.unique_emails)} unique, "
          f"{_fmt(c.duplicates)} duplicates")
    print(f"verdicts   valid {_fmt(c.valid)}  risky {_fmt(c.risky)}  "
          f"unknown {_fmt(c.unknown)}  invalid {_fmt(c.invalid)}  "
          f"disposable {_fmt(c.disposable)}")

    ok = c.total_rows == rows and c.unique_emails + c.duplicates == rows
    print()
    print("row accounting:", "ok" if ok else "MISMATCH — unique + duplicates != rows")
    return 0 if ok else 1


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--rows", type=int, default=1_000_000)
    ap.add_argument("--unique-ratio", type=float, default=0.8)
    ap.add_argument("--domains", type=int, default=150_000)
    ap.add_argument("--chunk", type=int, default=5_000)
    ap.add_argument("--concurrency", type=int, default=50)
    ap.add_argument("--keep", action="store_true", help="leave the temp directory behind")
    args = ap.parse_args()

    try:
        return asyncio.run(
            soak(args.rows, args.unique_ratio, args.domains, args.chunk, args.concurrency)
        )
    finally:
        if args.keep:
            print(f"\ntemp dir kept at {_TMP}")
        else:
            shutil.rmtree(_TMP, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
