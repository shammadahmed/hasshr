"""Measure classifier results against tests/command_corpus.json.

Run from the project root with: python tests/evaluate_safety.py
Commands are classified only; they are NEVER executed.
"""
from __future__ import annotations

import json
from collections import Counter, defaultdict
from pathlib import Path

from termiai.contracts import Context
from termiai.safety.classifier import classify

CORPUS = Path(__file__).with_name("command_corpus.json")

def main() -> int:
    data = json.loads(CORPUS.read_text(encoding="utf-8"))
    totals = Counter()
    matrix: dict[str, Counter[str]] = defaultdict(Counter)
    mismatches = []
    for case in data["cases"]:
        expected = case["expected"]
        actual = classify(case["command"], Context()).level.name
        totals["total"] += 1
        matrix[expected][actual] += 1
        if expected == actual:
            totals["correct"] += 1
        else:
            mismatches.append((case["command"], expected, actual, case["reason"]))
    accuracy = 100.0 * totals["correct"] / totals["total"] if totals["total"] else 0.0
    print(f"Safety classifier evaluation: {totals['correct']}/{totals['total']} correct ({accuracy:.1f}%)")
    print("\nConfusion counts (expected -> actual):")
    for expected in ("SAFE", "MODERATE", "SENSITIVE", "BLOCKED"):
        if expected in matrix:
            print(f"  {expected}: {dict(matrix[expected])}")
    print(f"\nMismatches: {len(mismatches)}")
    for command, expected, actual, reason in mismatches[:50]:
        print(f"  [{expected} -> {actual}] {command!r} ({reason})")
    if len(mismatches) > 50:
        print(f"  ... and {len(mismatches)-50} more")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
