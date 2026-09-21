"""Validate the evaluator before trusting it to judge a model.

The evaluator decides every number this project reports, so it gets checked in
both directions:

    --mode identity   score the gold query against itself       -> expect 100%
    --mode rewrite    score a semantics-preserving rewrite       -> expect ~100%
    --mode mutation   score a deliberately meaning-changing edit -> expect ~0%

Identity alone only proves the plumbing works. The rewrite pass is what shows
the metric is not secretly comparing SQL text or column names, and the mutation
pass is what shows it is not so lenient that everything matches.

Usage:
    python scripts/02_eval_gold.py                  # all three modes, full dev
    python scripts/02_eval_gold.py --limit 200
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# Spider contains non-ASCII values and Windows consoles default to a legacy
# codepage, which turns a printed row into UnicodeEncodeError.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from src import config, dataset  # noqa: E402
from src.evaluate import Summary, has_top_level_order_by, score  # noqa: E402
from src.execute import execute_sql  # noqa: E402


def rewrite(gold_sql: str) -> str | None:
    """A different query string with the same meaning, or None if unsafe.

    Wrapping in a subquery renames the output columns, which is exactly the
    point: a correct evaluator compares values, not column headers. A trailing
    comment is tacked on to exercise comment handling end to end.

    Skipped when the gold query has a top-level ORDER BY, since SQLite does not
    promise to preserve a subquery's ordering through the wrapper.
    """
    if has_top_level_order_by(gold_sql):
        return None
    return f"SELECT * FROM (\n{gold_sql.rstrip().rstrip(';')}\n) -- wrapped"


def mutate(gold_sql: str) -> str:
    """The same query truncated to one row -- same shape, different answer.

    Applied through a wrapper so it is valid even when the gold query already
    ends in LIMIT. Only used on questions whose gold result has 2+ rows, where
    the answer is guaranteed to change.
    """
    return f"SELECT * FROM (\n{gold_sql.rstrip().rstrip(';')}\n) LIMIT 1"


def run(mode: str, examples: list[dataset.Example]) -> tuple[Summary, list[str]]:
    summary = Summary()
    notable: list[str] = []

    for ex in examples:
        if mode == "identity":
            prediction = ex.gold_sql
        elif mode == "rewrite":
            prediction = rewrite(ex.gold_sql)
            if prediction is None:
                continue  # ordered queries are excluded, by design
        else:
            gold_result = execute_sql(ex.db_id, ex.gold_sql)
            if not gold_result.ok or len(gold_result.rows) < 2:
                continue  # truncating to 1 row would not change the answer
            prediction = mutate(ex.gold_sql)

        judgement = score(ex.db_id, ex.gold_sql, prediction)
        summary.add(judgement)

        # Record whichever direction is the surprise for this mode.
        unexpected = judgement.correct if mode == "mutation" else not judgement.correct
        if unexpected and len(notable) < 15:
            notable.append(
                f"  #{ex.index} {ex.db_id} [{judgement.reason}]\n"
                f"     gold: {' '.join(ex.gold_sql.split())}"
            )

    return summary, notable


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=0, help="0 means the whole dev set")
    parser.add_argument(
        "--mode",
        choices=["identity", "rewrite", "mutation", "all"],
        default="all",
    )
    args = parser.parse_args()

    config.require_data()
    examples = dataset.load_dev(limit=args.limit or None)
    modes = ["identity", "rewrite", "mutation"] if args.mode == "all" else [args.mode]

    expectations = {"identity": "100%", "rewrite": "~100%", "mutation": "~0%"}
    failed = False

    for mode in modes:
        print("=" * 68)
        print(f"mode: {mode}   (expected execution accuracy: {expectations[mode]})")
        print("=" * 68)
        summary, notable = run(mode, examples)
        print(summary.report())
        if notable:
            label = "scored correct but should not have" if mode == "mutation" else "scored wrong"
            print(f"\n{label}:")
            print("\n".join(notable))
        print()

        # The mutation pass is allowed a small residue: a handful of questions
        # have a gold query whose extra rows are duplicates of the first.
        if mode == "identity" and summary.accuracy < 1.0:
            failed = True
        if mode == "rewrite" and summary.accuracy < 0.99:
            failed = True
        if mode == "mutation" and summary.accuracy > 0.02:
            failed = True

    if failed:
        print("evaluator did NOT behave as expected -- investigate before running a model.")
        return 1
    print("evaluator behaves as expected in all three directions.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
