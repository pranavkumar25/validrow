"""Layer 3 — typo suggestion for fat-finger domains (`gmial.com` -> `gmail.com`).

Pure-python (no external distance lib) so the engine has zero extra deps for
this layer. Suggestions are advisory — we never mutate the address.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

_DATA = Path(__file__).resolve().parent.parent / "data"

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


def _damerau_levenshtein(a: str, b: str) -> int:
    """Optimal string alignment distance (adjacent transpositions count as 1)."""
    la, lb = len(a), len(b)
    if la == 0:
        return lb
    if lb == 0:
        return la
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
        prev2, prev = prev, cur
    return prev[lb]


def suggest_domain(domain: str) -> str | None:
    """Return a corrected domain if ``domain`` looks like a typo, else None."""
    domain = domain.lower().strip().rstrip(".")
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
    best: str | None = None
    best_dist = 99
    for top in tops:
        dist = _damerau_levenshtein(domain, top)
        # Guard precision: allow at most 1 edit for short domains, 2 for longer.
        limit = 1 if len(top) <= 8 else 2
        if dist <= limit and dist < best_dist:
            best, best_dist = top, dist

    if best is not None and best_dist <= 2:
        return best
    return candidate
