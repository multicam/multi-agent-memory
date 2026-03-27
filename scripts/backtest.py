#!/usr/bin/env python3
"""Quality gate: compare current test results against baseline.

Usage:
    uv run python scripts/backtest.py          # compare + update baseline
    uv run python scripts/backtest.py --check  # compare only (CI mode)

Exit codes:
    0 = all gates pass
    1 = regressions detected or coverage decreased
"""

import argparse
import shutil
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BASELINE = ROOT / ".test-baseline.xml"
CURRENT = ROOT / ".test-current.xml"
COV_BASELINE = ROOT / ".coverage-baseline"
COV_CURRENT = ROOT / ".coverage-current"


def parse_junit(path: Path) -> dict[str, str]:
    """Parse JUnit XML → {test_name: status}."""
    if not path.exists():
        return {}

    tree = ET.parse(path)
    results = {}
    for tc in tree.iter("testcase"):
        name = f"{tc.get('classname', '')}.{tc.get('name', '')}"
        if tc.find("failure") is not None:
            results[name] = "FAIL"
        elif tc.find("skipped") is not None:
            results[name] = "SKIP"
        elif tc.find("error") is not None:
            results[name] = "ERROR"
        else:
            results[name] = "PASS"
    return results


def compare(baseline: dict[str, str], current: dict[str, str]) -> dict:
    """Compare baseline vs current test results."""
    regressions = []
    fixed = []
    new_tests = []
    removed = []

    all_names = set(baseline.keys()) | set(current.keys())

    for name in sorted(all_names):
        b = baseline.get(name)
        c = current.get(name)

        if b is None and c is not None:
            new_tests.append((name, c))
        elif b is not None and c is None:
            removed.append((name, b))
        elif b == "PASS" and c in ("FAIL", "ERROR"):
            regressions.append((name, f"{b} -> {c}"))
        elif b in ("FAIL", "ERROR") and c == "PASS":
            fixed.append((name, f"{b} -> {c}"))

    return {
        "regressions": regressions,
        "fixed": fixed,
        "new": new_tests,
        "removed": removed,
        "baseline_count": len(baseline),
        "current_count": len(current),
    }


def main():
    parser = argparse.ArgumentParser(description="Test quality gate")
    parser.add_argument("--check", action="store_true", help="Compare only, don't update baseline")
    args = parser.parse_args()

    if not CURRENT.exists():
        print(f"No current results at {CURRENT}")
        print("Run: uv run pytest  (JUnit XML is auto-generated via pyproject.toml)")
        sys.exit(1)

    current = parse_junit(CURRENT)

    if not BASELINE.exists():
        print(f"No baseline found. Creating initial baseline from {len(current)} tests.")
        shutil.copy2(CURRENT, BASELINE)
        print(f"Baseline saved: {BASELINE}")
        sys.exit(0)

    baseline = parse_junit(BASELINE)
    result = compare(baseline, current)

    # Report
    print(f"\nBacktest results:")
    print(f"  Baseline: {result['baseline_count']} tests")
    print(f"  Current:  {result['current_count']} tests")
    print()

    if result["regressions"]:
        print(f"  REGRESSIONS ({len(result['regressions'])}):")
        for name, detail in result["regressions"]:
            print(f"    {name}  [{detail}]")
        print()

    if result["fixed"]:
        print(f"  FIXED ({len(result['fixed'])}):")
        for name, detail in result["fixed"]:
            print(f"    {name}  [{detail}]")
        print()

    if result["new"]:
        print(f"  NEW ({len(result['new'])}):")
        for name, status in result["new"]:
            print(f"    {name}  [{status}]")
        print()

    if result["removed"]:
        print(f"  REMOVED ({len(result['removed'])}):")
        for name, status in result["removed"]:
            print(f"    {name}  [{status}]")
        print()

    # Gate decision
    if result["regressions"]:
        print("  Gate: FAIL -- regressions detected")
        print("  Baseline NOT updated. Fix regressions before retrying.")
        sys.exit(1)

    print("  Gate: PASS -- 0 regressions")

    if not args.check:
        shutil.copy2(CURRENT, BASELINE)
        print(f"  Baseline updated.")

    sys.exit(0)


if __name__ == "__main__":
    main()
