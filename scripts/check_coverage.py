"""Enforce per-area coverage thresholds (docs/architecture/09-quality-gates-testing.md §2.3).

Usage: pytest --cov --cov-report=json && python scripts/check_coverage.py coverage.json
"""

import fnmatch
import json
import sys

# pattern -> (min line %, min branch %)
CRITICAL = {
    "gnosis/knowledge/lifecycle.py": (95.0, 90.0),
    "gnosis/context/*.py": (95.0, 90.0),
    "services/gnosis_api/auth.py": (95.0, 90.0),
}


def main(path: str) -> int:
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    failures = []
    for filename, info in data["files"].items():
        for pattern, (min_line, min_branch) in CRITICAL.items():
            if not fnmatch.fnmatch(filename, pattern):
                continue
            s = info["summary"]
            line = 100.0 * s["covered_lines"] / s["num_statements"] if s["num_statements"] else 100.0
            branch = 100.0 * s["covered_branches"] / s["num_branches"] if s.get("num_branches") else 100.0
            if line < min_line or branch < min_branch:
                failures.append(
                    f"{filename}: line {line:.1f}% (min {min_line}), branch {branch:.1f}% (min {min_branch})"
                )
    for f in failures:
        print("COVERAGE GATE FAILED", f)
    if not failures:
        print("Critical-area coverage gates passed.")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "coverage.json"))
