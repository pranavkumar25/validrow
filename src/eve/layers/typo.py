"""Layer 3 — typo suggestion for fat-finger domains (`gmial.com` -> `gmail.com`).

Pure-python (no external distance lib) so the engine has zero extra deps for
this layer. Suggestions are advisory — we never mutate the address.

This layer dominated the offline pipeline: 91% of `validate()` was spent here,
comparing every address's domain against every known domain, character by
character. Three things fix that without changing a single suggestion — the
answer is cached per domain, candidates whose length already rules them out are
never compared, and the distance stops as soon as it exceeds what could be
accepted. See `scripts/soak.py` for the measurement.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Optional

_DATA = Path(__file__).resolve().parent.parent / "data"

#: The most edits any candidate is ever accepted at (see ``_limit_for``). Used
#: to abandon a comparison early — nothing above this can win.
_MAX_LIMIT = 2

# Common second-level typo'd TLD -> correct TLD.
_TLD_FIXES = {
    "con": "com",
    "cim": "com",
    "cpm": "com",
    "vom": "com",
    "comm": "com",
    "co": "com",  # only applied as a suggestion, not a rewrite
    "ocm": "com",
    "cmo": "com",
    "nte": "net",
    "ne": "net",
    "ogr": "org",
    "or": "org",
}


@lru_cache(maxsize=1)
def _top_domains() -> list[str]:
    path = _DATA / "top_domains.txt"
    if not path.exists():
        return []
    return [
        line.strip().lower()
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.startswith("#")
    ]


def _limit_for(top: str) -> int:
    """Edits allowed against ``top``. Short domains get one, to guard precision."""
    return 1 if len(top) <= 8 else _MAX_LIMIT


def _damerau_levenshtein(a: str, b: str, max_dist: Optional[int] = None) -> int:
    """Optimal string alignment distance (adjacent transpositions count as 1).

    ``max_dist`` abandons the comparison once every alignment in progress is
    already worse than that, returning ``max_dist + 1``. The caller only ever
    asks "is this within the limit", so an exact value above it is wasted work —
    and at this size most candidates are rejected on the first or second row.
    """
    la, lb = len(a), len(b)
    if la == 0:
        return lb
    if lb == 0:
        return la
    if max_dist is not None and abs(la - lb) > max_dist:
        # Length alone already forces more edits than are allowed.
        return max_dist + 1

    prev2: list[int] = []
    prev = list(range(lb + 1))
    for i in range(1, la + 1):
        cur = [i] + [0] * lb
        for j in range(1, lb + 1):
            cost = 0 if a[i - 1] == b[j - 1] else 1
            cur[j] = min(
                prev[j] + 1,        # deletion
                cur[j - 1] + 1,     # insertion
                prev[j - 1] + cost,  # substitution
            )
            if (
                i > 1
                and j > 1
                and a[i - 1] == b[j - 2]
                and a[i - 2] == b[j - 1]
            ):
                cur[j] = min(cur[j], prev2[j - 2] + 1)  # transposition
        if max_dist is not None and min(cur) > max_dist:
            # Every alignment through this row already costs too much, and the
            # cost never decreases as rows are added.
            return max_dist + 1
        prev2, prev = prev, cur
    return prev[lb]


def suggest_domain(domain: str) -> Optional[str]:
    """Return a corrected domain if ``domain`` looks like a typo, else None."""
    return _suggest(domain.lower().strip().rstrip("."))


@lru_cache(maxsize=100_000)
def _suggest(domain: str) -> Optional[str]:
    """The pure lookup, cached by domain.

    A 1M-row list holds far fewer distinct domains than addresses, and every
    address on a domain gets the same answer — so this is computed once per
    domain per process rather than once per address.
    """
    if not domain or "." not in domain:
        return None

    tops = _top_domains()
    if domain in tops:
        return None

    name, _, tld = domain.rpartition(".")

    # 1) Straight TLD fix (gmail.con -> gmail.com) when the name is a known one.
    if tld in _TLD_FIXES:
        fixed = f"{name}.{_TLD_FIXES[tld]}"
        if fixed in tops or _TLD_FIXES[tld] in {"com", "net", "org"}:
            # Prefer a full known-domain match if we have one.
            if fixed in tops:
                return fixed
            candidate = fixed
        else:
            candidate = None
    else:
        candidate = None

    # 2) Nearest known domain within edit distance <= 2 (and shorter for short names).
    best: Optional[str] = None
    best_dist = 99
    length = len(domain)
    for top in tops:
        limit = _limit_for(top)
        if abs(length - len(top)) > limit:
            continue  # cannot possibly be within the limit
        dist = _damerau_levenshtein(domain, top, max_dist=limit)
        if dist <= limit and dist < best_dist:
            best, best_dist = top, dist
            if best_dist == 1:
                # Nothing can beat one edit: distance 0 would mean the domain is
                # itself a known one, which returned above.
                break

    if best is not None and best_dist <= _MAX_LIMIT:
        return best
    return candidate
