"""Streaming CSV read / column-detection / write.

Everything here streams: we never materialize the whole sheet, so a 1M-row file
uses flat memory. Column detection reads only the header + a small sample.
"""
from __future__ import annotations

import csv
import io
import tempfile
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Optional

from eve.storage import ObjectStore

# Header names we'll auto-guess as the email column, in priority order.
_EMAIL_HINTS = ["email", "email_address", "e-mail", "mail", "emailaddress", "work_email"]
_FIRST_HINTS = ["first_name", "firstname", "first", "fname", "given_name"]
_LAST_HINTS = ["last_name", "lastname", "last", "lname", "surname", "family_name"]


@dataclass
class ColumnDetection:
    columns: list[str]
    sample_rows: list[dict[str, str]]
    guessed_email: Optional[str]
    guessed_first_name: Optional[str]
    guessed_last_name: Optional[str]
    delimiter: str
    # Line ending detected in the source file. We only *validate the email*, so
    # the rest of the CSV — delimiter, quoting, line endings, every other column
    # — is passed through untouched; the output mirrors whatever was uploaded.
    line_terminator: str = "\n"


def _text_reader(store: ObjectStore, key: str):
    """Open a stored object as a text stream (BOM-aware)."""
    return store.open_read(key)


def _guess(columns: list[str], hints: list[str]) -> Optional[str]:
    lowered = {c.lower().strip(): c for c in columns}
    for h in hints:
        if h in lowered:
            return lowered[h]
    # substring fallback
    for low, orig in lowered.items():
        if any(h in low for h in hints):
            return orig
    return None


async def detect_columns(store: ObjectStore, key: str, sample: int = 5) -> ColumnDetection:
    fh = await _text_reader(store, key)
    try:
        raw = fh.read(65536)
    finally:
        fh.close()
    text = raw.decode("utf-8-sig", errors="replace") if isinstance(raw, bytes) else raw
    try:
        dialect = csv.Sniffer().sniff(text, delimiters=",;\t|")
        delimiter = dialect.delimiter
    except csv.Error:
        delimiter = ","

    # Preserve the source file's line ending (CRLF vs LF) on the way out.
    line_terminator = "\r\n" if "\r\n" in text else "\n"

    reader = csv.DictReader(io.StringIO(text), delimiter=delimiter)
    columns = reader.fieldnames or []
    sample_rows: list[dict[str, str]] = []
    for i, row in enumerate(reader):
        if i >= sample:
            break
        # A ragged row (more cells than headers) makes DictReader stash the
        # overflow under a None key. Formatting can be anything, so never let
        # that crash detection — drop the None key and coerce values to str.
        sample_rows.append({k: ("" if v is None else v) for k, v in row.items() if k is not None})

    return ColumnDetection(
        columns=list(columns),
        sample_rows=sample_rows,
        guessed_email=_guess(columns, _EMAIL_HINTS),
        guessed_first_name=_guess(columns, _FIRST_HINTS),
        guessed_last_name=_guess(columns, _LAST_HINTS),
        delimiter=delimiter,
        line_terminator=line_terminator,
    )


@dataclass
class PreviewStats:
    """What the file actually contains, counted rather than estimated.

    The product tells you what you will be charged for *before* you spend
    anything, so this number has to come from a real pass over the file — an
    estimate from the first 64KB would be a guess presented as a price.
    """

    total_rows: int
    unique_emails: int
    blank_emails: int


async def preview_stats(
    store: ObjectStore, key: str, email_column: str, delimiter: str = ","
) -> PreviewStats:
    """Count rows and unique deliverable-candidate addresses in one pass."""
    from eve.layers.normalize import normalize
    from eve.layers.syntax import check_syntax

    total = 0
    blank = 0
    seen: set[str] = set()
    async for row in iter_rows(store, key, delimiter):
        total += 1
        raw = (row.get(email_column) or "").strip()
        if not raw:
            blank += 1
            continue
        syn = check_syntax(raw)
        if syn.valid:
            seen.add(normalize(syn.local_part or "", syn.domain or "").dedupe_key)
        else:
            # Malformed addresses are still charged work — they are validated
            # (and rejected) at layer 1 — so they count toward the unique total.
            seen.add(raw.lower())
    return PreviewStats(total_rows=total, unique_emails=len(seen), blank_emails=blank)


async def iter_rows(
    store: ObjectStore, key: str, delimiter: str = ","
) -> Iterator[dict[str, str]]:
    """Stream rows as dicts. Loads one row at a time."""
    fh = await store.open_read(key)
    text = io.TextIOWrapper(fh, encoding="utf-8-sig", newline="")
    try:
        reader = csv.DictReader(text, delimiter=delimiter)
        for row in reader:
            yield row
    finally:
        text.close()


#: Bytes an output CSV may hold in memory before it spills to a temp file. The
#: assembly pass writes three of these at once, so this is the per-writer share
#: of a bounded budget, not a per-job one.
SPILL_BYTES = 4 * 1024 * 1024


class CsvWriter:
    """CSV writer that spills to disk, then streams to an ObjectStore key on close.

    It used to hold the whole output in a StringIO. That made peak memory scale
    with *rows* — a 1M-row run spent ~500 MB on three output buffers — which
    quietly contradicted the pipeline's O(unique) claim, since the assembly pass
    is otherwise a pure stream. Small jobs still never touch the disk: the spill
    only happens once a writer passes ``SPILL_BYTES``.
    """

    def __init__(
        self,
        store: ObjectStore,
        key: str,
        header: list[str],
        *,
        delimiter: str = ",",
        line_terminator: str = "\n",
        spill_bytes: int = SPILL_BYTES,
    ):
        self.store = store
        self.key = key
        self.header = header
        self._buf = io.StringIO()
        self._spill_bytes = spill_bytes
        self._sink = None  # opened lazily, on the first spill
        # CSV formatting is not fixed — we only validate the email and echo the
        # rest back. Mirror the source file's delimiter and line ending (csv's
        # own default is CRLF) so the output keeps whatever format was uploaded.
        self._writer = csv.DictWriter(
            self._buf,
            fieldnames=header,
            extrasaction="ignore",
            delimiter=delimiter,
            lineterminator=line_terminator,
        )
        self._writer.writeheader()
        self.rows = 0

    def write(self, row: dict[str, object]) -> None:
        self._writer.writerow(row)
        self.rows += 1
        if self._buf.tell() >= self._spill_bytes:
            self._spill()

    def _spill(self) -> None:
        """Move what is buffered onto disk and reset the buffer."""
        text = self._buf.getvalue()
        if not text:
            return
        if self._sink is None:
            self._sink = tempfile.TemporaryFile(suffix=".csv")
        self._sink.write(text.encode("utf-8"))
        self._buf.seek(0)
        self._buf.truncate(0)

    async def close(self) -> None:
        if self._sink is None:
            # Never spilled: the whole file is small, so hand it over directly.
            await self.store.put(self.key, io.BytesIO(self._buf.getvalue().encode("utf-8")))
        else:
            self._spill()
            self._sink.flush()
            self._sink.seek(0)
            try:
                await self.store.put(self.key, self._sink)
            finally:
                self._sink.close()
                self._sink = None
        self._buf.close()
