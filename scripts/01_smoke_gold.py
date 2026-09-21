"""Can we execute Spider's own gold SQL?

Walks the first N dev questions, runs the gold query against the matching
database through the sandbox, and prints what comes back. Nothing here involves
a model -- the point is to prove that the data layout, the read-only connection
and the result plumbing all work before any of them can be blamed for a low
accuracy number later.

Usage:
    python scripts/01_smoke_gold.py            # first 20
    python scripts/01_smoke_gold.py --limit 0  # whole dev set
    python scripts/01_smoke_gold.py --quiet    # failures only
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# Spider contains non-ASCII values and Windows consoles default to a legacy
# codepage, which turns a printed row into UnicodeEncodeError.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from src import config, dataset  # noqa: E402
from src.execute import describe, execute_sql  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=20, help="0 means the whole dev set")
    parser.add_argument("--quiet", action="store_true", help="print failures only")
    parser.add_argument("--timeout", type=float, default=10.0)
    args = parser.parse_args()

    config.require_data()
    examples = dataset.load_dev(limit=args.limit or None)
    print(f"running gold SQL for {len(examples)} dev question(s)\n")

    failures: list[tuple[dataset.Example, str]] = []
    kinds: Counter[str] = Counter()
    empty = 0

    for ex in examples:
        result = execute_sql(ex.db_id, ex.gold_sql, timeout_s=args.timeout)
        if not result.ok:
            failures.append((ex, f"[{result.error_kind}] {result.error}"))
            kinds[result.error_kind or "unknown"] += 1
        elif not result.rows:
            empty += 1

        if args.quiet and result.ok:
            continue
        status = "ok " if result.ok else "FAIL"
        print(f"[{status}] #{ex.index}  db={ex.db_id}")
        print(f"       Q: {ex.question}")
        print(f"     SQL: {' '.join(ex.gold_sql.split())}")
        for line in describe(result, max_rows=3).splitlines():
            print(f"      {line}")
        print()

    total = len(examples)
    print("-" * 66)
    print(f"executed : {total}")
    print(f"succeeded: {total - len(failures)}")
    print(f"failed   : {len(failures)}  {dict(kinds) if kinds else ''}")
    # An empty result set is legal SQL, but it is also the case where execution
    # accuracy is easiest to pass by accident, so it is worth counting.
    print(f"empty set: {empty} (of the successful ones)")

    if failures:
        print("\nfailures:")
        for ex, msg in failures:
            print(f"  #{ex.index} {ex.db_id}: {msg}")
            print(f"     {' '.join(ex.gold_sql.split())}")
        return 1

    print("\nall gold queries executed cleanly.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
