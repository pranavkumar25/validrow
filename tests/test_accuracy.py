"""Accuracy gate — runs the engine over the labeled fixture set (offline).

Reports per-class accuracy + a confusion matrix and fails if overall accuracy
regresses below threshold. This harness is reused in M4 against a larger
ground-truth dataset.
"""
from __future__ import annotations

import csv
from collections import Counter
from pathlib import Path

from eve import validate

FIXTURES = Path(__file__).parent / "fixtures" / "labeled.csv"
THRESHOLD = 0.95


def _rows():
    with FIXTURES.open(encoding="utf-8") as fh:
        yield from csv.DictReader(fh)


def test_status_accuracy():
    total = 0
    correct = 0
    confusion: Counter = Counter()
    mismatches = []

    for row in _rows():
        total += 1
        verdict = validate(row["email"], enable_dns=False, enable_smtp=False)
        predicted = verdict.status.value
        expected = row["expected_status"]
        confusion[(expected, predicted)] += 1
        if predicted == expected:
            correct += 1
        else:
            mismatches.append((row["email"], expected, predicted))

    accuracy = correct / total if total else 0.0
    report = "\n".join(f"  {e} -> {p}: {n}" for (e, p), n in sorted(confusion.items()))
    detail = "\n".join(f"  {m[0]}: expected {m[1]}, got {m[2]}" for m in mismatches)
    print(f"\nStatus accuracy: {accuracy:.1%} ({correct}/{total})")
    print("Confusion (expected -> predicted):\n" + report)
    if mismatches:
        print("Mismatches:\n" + detail)

    assert accuracy >= THRESHOLD, f"accuracy {accuracy:.1%} < {THRESHOLD:.0%}\n{detail}"


def test_typo_suggestions():
    for row in _rows():
        expected = row["expected_suggestion"].strip()
        if not expected:
            continue
        verdict = validate(row["email"], enable_dns=False, enable_smtp=False)
        assert verdict.suggested_correction, f"no suggestion for {row['email']}"
        assert verdict.suggested_correction.endswith("@" + expected), (
            f"{row['email']}: expected suggestion @{expected}, got {verdict.suggested_correction}"
        )
