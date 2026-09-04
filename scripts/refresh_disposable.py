"""Re-merge the vendored disposable-domain list with its upstream source.

Run via `make disposable`. Two things it will not do:

* **Silently drop local entries.** Anything under the local-additions marker
  that upstream still lacks is carried forward — upstream removes domains as
  well as adding them, and a refresh that quietly discarded our own findings
  would be a downgrade wearing an upgrade's name.
* **Accept a list that marks real mail undeliverable.** If any incoming domain
  also appears in `free.txt` or `top_domains.txt`, it refuses and prints the
  collisions. A false positive here does not degrade a verdict, it inverts one:
  a real person's address is reported as a throwaway.

Anything in `disposable_exclusions.txt` is removed on every refresh, so a
hand-made judgement cannot be silently undone by an upstream that disagrees.
"""
from __future__ import annotations

import sys
import urllib.request
from pathlib import Path

SOURCE = (
    "https://raw.githubusercontent.com/disposable-email-domains/"
    "disposable-email-domains/main/disposable_email_blocklist.conf"
)
DATA = Path(__file__).resolve().parent.parent / "src" / "eve" / "data"
TARGET = DATA / "disposable.txt"
EXCLUSIONS = DATA / "disposable_exclusions.txt"
LOCAL_MARKER = "# --- local additions"
UPSTREAM_MARKER = "# --- upstream ---"


def _entries(path: Path) -> list[str]:
    return [
        ln.strip().lower()
        for ln in path.read_text(encoding="utf-8").splitlines()
        if ln.strip() and not ln.startswith("#")
    ]


def _local_additions(path: Path) -> list[str]:
    """Everything below the local marker — the entries upstream does not have."""
    if not path.exists():
        return []
    out, seen_marker = [], False
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith(LOCAL_MARKER):
            seen_marker = True
            continue
        if seen_marker and line.strip() and not line.startswith("#"):
            out.append(line.strip().lower())
    return out


def main() -> int:
    header = []
    for line in TARGET.read_text(encoding="utf-8").splitlines():
        if line.startswith(UPSTREAM_MARKER):
            break
        header.append(line)

    previous_local = _local_additions(TARGET)
    print(f"fetching {SOURCE}")
    with urllib.request.urlopen(SOURCE, timeout=60) as resp:  # noqa: S310 - fixed https URL
        upstream = sorted({
            ln.strip().lower()
            for ln in resp.read().decode("utf-8").splitlines()
            if ln.strip() and not ln.startswith("#")
        })
    if len(upstream) < 1000:
        print(f"refusing: upstream returned only {len(upstream)} domains", file=sys.stderr)
        return 1

    # A domain cannot be both a throwaway and a mailbox people actually keep.
    protected = set(_entries(DATA / "free.txt")) | set(_entries(DATA / "top_domains.txt"))
    collisions = sorted(set(upstream) & protected)
    if collisions:
        print("refusing: upstream lists domains we treat as real mail:", file=sys.stderr)
        for c in collisions:
            print(f"  {c}", file=sys.stderr)
        return 1

    # Domains upstream calls disposable that this engine reports as typos.
    excluded = set(_entries(EXCLUSIONS)) if EXCLUSIONS.exists() else set()
    dropped = sorted(set(upstream) & excluded)
    upstream = [d for d in upstream if d not in excluded]

    kept = sorted(set(previous_local) - set(upstream) - excluded)
    adopted = sorted(set(previous_local) & set(upstream))

    TARGET.write_text(
        "\n".join(
            header
            + [UPSTREAM_MARKER]
            + upstream
            + ["#", f"{LOCAL_MARKER} (absent upstream; preserved by the refresh) ---"]
            + kept
        )
        + "\n",
        encoding="utf-8",
    )

    print(f"upstream {len(upstream):,} · local kept {len(kept)} · total {len(upstream) + len(kept):,}")
    if dropped:
        print(f"excluded {len(dropped)} typo domain(s): {', '.join(dropped)}")
    if adopted:
        print(f"upstream has caught up on {len(adopted)}: {', '.join(adopted)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
