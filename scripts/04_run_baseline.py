"""Run a generation configuration over the dev set and score it.

Writes one JSONL record per question -- prompt inputs, raw response, extracted
SQL, the judgement and why -- because the aggregate number is not what the
error analysis needs. Re-running is cheap: every model response is cached, so a
second run of the same configuration costs nothing and produces byte-identical
output.

Usage:
    python scripts/04_run_baseline.py --limit 200          # tuning slice
    python scripts/04_run_baseline.py --limit 0            # full dev set
    python scripts/04_run_baseline.py --limit 0 --offline  # from cache only
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from src import config, dataset  # noqa: E402
from src.evaluate import Summary, score  # noqa: E402
from src.generate import generate_sql  # noqa: E402
from src.llm import DEFAULT_MODEL, LLMClient  # noqa: E402

# gpt-4o-mini list price, USD per million tokens. Only used for the cost line
# in the run summary -- close enough to keep an eye on spending.
PRICE_IN, PRICE_OUT = 0.15, 0.60


def run_one(client: LLMClient, example: dataset.Example) -> dict:
    """Generate, score, and return the record for one question."""
    started = time.monotonic()
    try:
        generation = generate_sql(client, example.db_id, example.question)
    except Exception as exc:  # a failed call is a failed question, not a crash
        return {
            "qid": example.qid,
            "db_id": example.db_id,
            "question": example.question,
            "gold_sql": example.gold_sql,
            "predicted_sql": None,
            "raw_response": "",
            "correct": False,
            "reason": f"generation_failed:{type(exc).__name__}",
            "error": str(exc)[:300],
            "elapsed_s": round(time.monotonic() - started, 3),
        }

    judgement = score(example.db_id, example.gold_sql, generation.sql)
    return {
        "qid": example.qid,
        "db_id": example.db_id,
        "question": example.question,
        "gold_sql": example.gold_sql,
        "predicted_sql": generation.sql,
        "raw_response": generation.raw_response,
        "correct": judgement.correct,
        "reason": judgement.reason,
        "gold_rows": judgement.gold_rows,
        "pred_rows": judgement.pred_rows,
        "prompt_tokens": generation.prompt_tokens,
        "completion_tokens": generation.completion_tokens,
        "cached": generation.cached,
        "elapsed_s": round(time.monotonic() - started, 3),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=config.DEV_TUNING_SLICE,
                        help="0 means the whole dev set")
    parser.add_argument("--workers", type=int, default=8,
                        help="concurrent requests; lower this if rate-limited")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--offline", action="store_true",
                        help="serve from cache only; error on a miss")
    parser.add_argument("--tag", default="baseline", help="name for the output file")
    args = parser.parse_args()

    config.require_data()
    examples = dataset.load_dev(limit=args.limit or None)
    client = LLMClient(model=args.model, offline=args.offline)

    slice_label = f"dev[:{args.limit}]" if args.limit else "full dev set"
    print(f"config : {args.tag}")
    print(f"model  : {args.model}  (temperature 0)")
    print(f"data   : {slice_label}, {len(examples)} questions")
    print(f"workers: {args.workers}\n")

    started = time.monotonic()
    records: list[dict] = []
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        for i, record in enumerate(pool.map(lambda ex: run_one(client, ex), examples), 1):
            records.append(record)
            if i % 25 == 0 or i == len(examples):
                so_far = sum(r["correct"] for r in records)
                print(f"  {i:5d}/{len(examples)}  running accuracy {so_far / i:.1%}")
    wall = time.monotonic() - started

    summary = Summary()
    for record in records:
        # Rebuild the summary from the records so the file on disk and the
        # printed number can never disagree.
        from src.evaluate import Judgement

        summary.add(
            Judgement(record["correct"], record["reason"], record.get("gold_rows", 0),
                      record.get("pred_rows", 0))
        )

    config.RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    out_path = config.RESULTS_DIR / f"{args.tag}_{'full' if not args.limit else args.limit}.jsonl"
    with out_path.open("w", encoding="utf-8") as fh:
        for record in records:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")

    print("\n" + "=" * 62)
    print(summary.report())
    usage = client.usage
    cost = (usage.input_tokens * PRICE_IN + usage.output_tokens * PRICE_OUT) / 1e6
    print(
        f"\nLLM: {usage.calls} live calls, {usage.cache_hits} cache hits"
        f"  |  {usage.input_tokens:,} in / {usage.output_tokens:,} out tokens"
        f"  |  ~${cost:.3f}"
    )
    print(f"wall clock: {wall:.1f}s  ({wall / max(1, len(examples)):.2f}s per question)")
    print(f"records   : {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
