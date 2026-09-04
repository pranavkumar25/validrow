"""Streaming CSV read / column-detection / write.

Everything here streams: we never materialize the whole sheet, so a 1M-row file
uses flat memory. Column detection reads only the header + a small sample.
"""
from __future__ import annotations

import csv
import io
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


class CsvWriter:
    """Buffered CSV writer that streams to an ObjectStore key on close."""

    def __init__(
        self,
        store: ObjectStore,
        key: str,
        header: list[str],
        *,
        delimiter: str = ",",
        line_terminator: str = "\n",
    ):
        self.store = store
        self.key = key
        self.header = header
        self._buf = io.StringIO()
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

    async def close(self) -> None:
        data = io.BytesIO(self._buf.getvalue().encode("utf-8"))
        await self.store.put(self.key, data)
        self._buf.close()
