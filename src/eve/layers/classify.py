"""Layer 5 — classification: disposable / role-based / free vs corporate.

All three signals are data-driven (files under ``eve/data``) so lists can be
refreshed without a code change.
"""
from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

_DATA = Path(__file__).resolve().parent.parent / "data"


@lru_cache(maxsize=8)
def _load_set(filename: str) -> set[str]:
    path = _DATA / filename
    if not path.exists():
        return set()
    return {
        line.strip().lower()
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.startswith("#")
    }


def list_sizes() -> dict[str, int]:
    """How many entries each classification list carries.

    The landing page quotes the disposable count. Reading it from the file this
    layer loads is what stops the number on the page and the list in the engine
    from drifting apart after a `make disposable`.
    """
    return {name: len(_load_set(f"{name}.txt")) for name in ("disposable", "roles", "free")}


@dataclass
class ClassifyResult:
    is_disposable: bool
    is_role: bool
    is_free: bool


def classify(local_part: str, domain: str) -> ClassifyResult:
    domain = domain.lower().strip().rstrip(".")
    local = local_part.lower().strip()

    disposable = _load_set("disposable.txt")
    roles = _load_set("roles.txt")
    free = _load_set("free.txt")

    return ClassifyResult(
        is_disposable=domain in disposable,
        is_role=local in roles,
        is_free=domain in free,
    )
