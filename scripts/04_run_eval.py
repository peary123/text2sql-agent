"""Run one prompt configuration over the dev set and score it.

Writes one JSONL record per question -- prompt inputs, raw response, extracted
SQL, the judgement and why -- because the aggregate number is not what the
error analysis needs. Re-running is cheap: every model response is cached, so a
second run of the same configuration costs nothing and produces byte-identical
output.

Each prompt feature is its own flag, so a run differs from the one before it by
exactly one thing and the ablation can attribute the difference to it.

Usage:
    python scripts/04_run_eval.py --limit 200                     # baseline
    python scripts/04_run_eval.py --limit 200 --column-order      # one change
    python scripts/04_run_eval.py --limit 0 --column-order --only-requested-columns
    python scripts/04_run_eval.py --limit 0 --offline             # from cache
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

from src import config as paths  # noqa: E402
from src import dataset  # noqa: E402
from src.evaluate import Summary, score  # noqa: E402
from src.generate import PromptConfig  # noqa: E402
from src.repair import RepairPolicy, generate_with_repair  # noqa: E402
from src.llm import DEFAULT_MODEL, LLMClient  # noqa: E402

# gpt-4o-mini list price, USD per million tokens. Only used for the cost line
# in the run summary -- close enough to keep an eye on spending.
PRICE_IN, PRICE_OUT = 0.15, 0.60


def run_one(
    client: LLMClient,
    example: dataset.Example,
    config: PromptConfig,
    policy: RepairPolicy,
) -> dict:
    """Generate (and repair, if enabled), then score, one question."""
    started = time.monotonic()
    try:
        # The loop is given the question and the database, never the gold
        # query: a repair may only act on what a deployed system would know.
        trace = generate_with_repair(client, example.db_id, example.question, config, policy)
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

    judgement = score(example.db_id, example.gold_sql, trace.final_sql)

    # Scoring every intermediate attempt happens only after the loop has
    # finished, so gold never flows back into it. It is what lets the report
    # say how many questions a first repair fixed and how many a second did --
    # the evidence for (or against) the cap.
    attempt_correct = (
        [score(example.db_id, example.gold_sql, a.sql).correct for a in trace.attempts]
        if trace.triggered
        else [judgement.correct]
    )

    return {
        "qid": example.qid,
        "db_id": example.db_id,
        "question": example.question,
        "gold_sql": example.gold_sql,
        "predicted_sql": trace.final_sql,
        "raw_response": trace.attempts[-1].raw,
        "correct": judgement.correct,
        "reason": judgement.reason,
        "gold_rows": judgement.gold_rows,
        "pred_rows": judgement.pred_rows,
        "column_permutation": judgement.column_permutation,
        "repairs_used": trace.repairs_used,
        "attempts": [
            {"sql": a.sql, "outcome": a.outcome, "error": a.error, "correct": ok}
            for a, ok in zip(trace.attempts, attempt_correct)
        ] if trace.triggered else [],
        "llm_calls": trace.llm_calls,
        "llm_latency_s": round(trace.llm_latency_s, 3),
        "prompt_tokens": trace.prompt_tokens,
        "completion_tokens": trace.completion_tokens,
        "cached": trace.cached,
        "elapsed_s": round(time.monotonic() - started, 3),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=paths.DEV_TUNING_SLICE,
                        help="0 means the whole dev set")
    parser.add_argument("--workers", type=int, default=8,
                        help="concurrent requests; lower this if rate-limited")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--offline", action="store_true",
                        help="serve from cache only; error on a miss")
    parser.add_argument("--only-requested-columns", action="store_true",
                        help="tell the model not to add unrequested columns")
    parser.add_argument("--column-order", action="store_true",
                        help="tell the model to order columns as the question does")
    parser.add_argument("--sample-rows", action="store_true",
                        help="show a few real rows under each CREATE TABLE")
    parser.add_argument("--column-values", action="store_true",
                        help="list the values of low-cardinality text columns")
    parser.add_argument("--repair", type=int, default=0, metavar="N",
                        help="on an execution error, send the error back up to N times")
    parser.add_argument("--repair-on-empty", action="store_true",
                        help="also repair queries that run but return no rows")
    parser.add_argument("--few-shot", type=int, default=0, metavar="K",
                        help="retrieve K question/SQL pairs from the train split")
    parser.add_argument("--tag", default=None, help="override the output file name")
    args = parser.parse_args()

    config = PromptConfig(
        only_requested_columns=args.only_requested_columns,
        column_order=args.column_order,
        sample_rows=args.sample_rows,
        column_values=args.column_values,
        few_shot=args.few_shot,
    )
    policy = RepairPolicy(max_repairs=args.repair, on_empty=args.repair_on_empty)
    tag = args.tag or "+".join(x for x in (config.tag, policy.tag) if x)

    paths.require_data()
    examples = dataset.load_dev(limit=args.limit or None)
    client = LLMClient(model=args.model, offline=args.offline)

    slice_label = f"stratified {args.limit}" if args.limit else "full dev set"
    print(f"config : {tag}")
    print(f"model  : {args.model}  (temperature 0)")
    print(f"data   : {slice_label}, {len(examples)} questions")
    print(f"workers: {args.workers}\n")

    started = time.monotonic()
    records: list[dict] = []
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        work = pool.map(lambda ex: run_one(client, ex, config, policy), examples)
        for i, record in enumerate(work, 1):
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
            Judgement(
                record["correct"],
                record["reason"],
                record.get("gold_rows", 0),
                record.get("pred_rows", 0),
                record.get("column_permutation", False),
            )
        )

    paths.RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    suffix = "full" if not args.limit else str(args.limit)
    out_path = paths.RESULTS_DIR / f"{tag}_{suffix}.jsonl"
    with out_path.open("w", encoding="utf-8") as fh:
        for record in records:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")

    print("\n" + "=" * 62)
    print(summary.report())
    print(_cost_and_repair_report(records))
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


def _cost_and_repair_report(records: list[dict]) -> str:
    """Calls, model latency, and -- when repair ran -- what it did.

    Latency is the provider's own response time, recorded when each response
    was first generated and served back from the cache, so it is the same
    number on every re-run. It is measured under 8 concurrent requests, which
    is how every configuration here was run.
    """
    n = max(1, len(records))
    calls = [r.get("llm_calls", 1) for r in records]
    lat = sorted(r.get("llm_latency_s", 0.0) for r in records)
    lines = [
        f"LLM calls / question: {sum(calls) / n:.3f}",
        f"model latency / question: mean {sum(lat) / n:.2f}s  "
        f"p50 {lat[len(lat) // 2]:.2f}s  p95 {lat[int(len(lat) * 0.95)]:.2f}s",
    ]
    triggered = [r for r in records if r.get("repairs_used", 0) > 0]
    if not triggered:
        return "\n".join(lines)

    fixed = sum(r["correct"] for r in triggered)
    now_runs = sum(r["attempts"][-1]["outcome"] in ("ok", "empty") for r in triggered)
    lines.append(
        f"repair triggered: {len(triggered)} questions "
        f"({len(triggered) / n:.1%}); after repair {now_runs} execute, {fixed} correct"
    )
    # How many triggered questions were correct after 0, 1, 2 ... repairs.
    depth = max(len(r["attempts"]) for r in triggered)
    for k in range(depth):
        ok = sum(
            r["attempts"][min(k, len(r["attempts"]) - 1)]["correct"] for r in triggered
        )
        lines.append(f"  correct after {k} repair(s): {ok}")
    return "\n".join(lines)


if __name__ == "__main__":
    raise SystemExit(main())
