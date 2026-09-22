"""Pull a sample of failures out of a run, ready to be read by a human.

Produces two files: a JSONL the labelling script reads back, and a Markdown
dump with the question, both queries, and what each one actually returned.

The result previews are the point. A predicted query that looks reasonable next
to the gold query is often obviously wrong the moment you see that it returned
0 rows because it compared a country column against 'United States' when the
database holds 'USA'. Classifying from the SQL alone gets that case wrong.

Usage:
    python scripts/05_sample_errors.py --run results/baseline_full.jsonl -n 50
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from src.execute import describe, execute_sql  # noqa: E402


def sample(failures: list[dict], n: int) -> list[dict]:
    """`n` failures spread across databases, proportional and deterministic.

    Same reasoning as the tuning slice: taking the first 50 would be 50
    questions about cars. Proportional to each database's share of the
    failures, floor of one, evenly spaced within each database.
    """
    by_db: dict[str, list[dict]] = {}
    for record in failures:
        by_db.setdefault(record["db_id"], []).append(record)

    total = len(failures)
    quota = {db: max(1, round(n * len(rows) / total)) for db, rows in by_db.items()}
    while sum(quota.values()) > n:
        biggest = max(quota, key=lambda db: (quota[db], db))
        if quota[biggest] <= 1:
            break
        quota[biggest] -= 1

    chosen: list[dict] = []
    for db, rows in by_db.items():
        k = min(quota[db], len(rows))
        step = len(rows) / k
        chosen.extend(rows[int(i * step)] for i in range(k))
    return sorted(chosen, key=lambda r: r["qid"])


def render(record: dict, index: int) -> str:
    """One failure, with enough context to classify it without running anything."""
    gold = execute_sql(record["db_id"], record["gold_sql"])
    pred = (
        execute_sql(record["db_id"], record["predicted_sql"])
        if record.get("predicted_sql")
        else None
    )

    lines = [
        f"## {index}. `{record['qid']}`  —  {record['reason']}",
        "",
        f"**Q:** {record['question']}",
        "",
        "```sql",
        "-- gold",
        " ".join(record["gold_sql"].split()),
        "",
        "-- predicted",
        " ".join((record["predicted_sql"] or "(nothing extracted)").split()),
        "```",
        "",
        "| | result |",
        "|---|---|",
        f"| gold | {describe(gold, max_rows=3).replace(chr(10), '<br>')} |",
        f"| predicted | {(describe(pred, max_rows=3).replace(chr(10), '<br>')) if pred else 'n/a'} |",
        "",
        "**label:** `TODO`",
        "",
    ]
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", default="results/baseline_full.jsonl")
    parser.add_argument("-n", type=int, default=50)
    parser.add_argument("--out", default="results/error_sample")
    args = parser.parse_args()

    run_path = ROOT / args.run
    records = [json.loads(line) for line in run_path.open(encoding="utf-8")]
    failures = [r for r in records if not r["correct"]]
    print(f"{len(records)} questions, {len(failures)} failures")

    chosen = sample(failures, args.n)
    by_db: dict[str, int] = {}
    for record in chosen:
        by_db[record["db_id"]] = by_db.get(record["db_id"], 0) + 1
    print(f"sampled {len(chosen)} across {len(by_db)} databases\n")
    for db, count in sorted(by_db.items(), key=lambda kv: -kv[1]):
        print(f"  {db:30s} {count}")

    jsonl_path = ROOT / f"{args.out}.jsonl"
    with jsonl_path.open("w", encoding="utf-8") as fh:
        for record in chosen:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")

    md_path = ROOT / f"{args.out}.md"
    body = [
        "# Error sample",
        "",
        f"{len(chosen)} of {len(failures)} failures from `{args.run}`, "
        "sampled proportionally across databases.",
        "",
        "Labels are filled in by hand and collected in "
        "`eval/error_labels.json`; run `scripts/06_error_report.py` to "
        "aggregate them.",
        "",
        "---",
        "",
    ]
    body += [render(record, i) for i, record in enumerate(chosen, 1)]
    md_path.write_text("\n".join(body), encoding="utf-8")

    print(f"\n  {jsonl_path}\n  {md_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
