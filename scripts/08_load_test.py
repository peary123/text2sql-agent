"""Fire concurrent /query requests at a running service and report latency.

Questions are the stratified dev slice, so every one of the 20 dev databases is
exercised. Each served answer is also scored against gold. Against a warm
server that is a consistency check -- the service should give exactly the
answers the offline evaluation measured, because it runs the same code on the
same cached responses -- and against a cold server it is live accuracy.

Usage:
    python scripts/08_load_test.py --url http://127.0.0.1:8000 --label warm
    python scripts/08_load_test.py --url http://127.0.0.1:8001 --label cold
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import httpx  # noqa: E402

from src import config, dataset  # noqa: E402
from src.evaluate import score  # noqa: E402

_local = threading.local()


def _http() -> httpx.Client:
    # One client per worker thread: connection reuse without sharing state.
    if not hasattr(_local, "client"):
        _local.client = httpx.Client(timeout=120.0)
    return _local.client


def percentile(sorted_values: list[float], p: float) -> float:
    """Nearest-rank percentile -- always a latency some request actually had."""
    if not sorted_values:
        return 0.0
    rank = max(1, math.ceil(p / 100 * len(sorted_values)))
    return sorted_values[rank - 1]


def fire(url: str, example: dataset.Example) -> dict:
    # Build (or fetch) this thread's client *before* starting the clock. Creating
    # an httpx.Client loads a CA bundle even for plain http, and the first
    # version of this script timed that: the first request on each of the 10
    # threads looked 15x slower than every other request, while the server's
    # own timings for those requests were normal.
    client = _http()
    started = time.perf_counter()
    try:
        response = client.post(
            f"{url}/query", json={"db_id": example.db_id, "question": example.question}
        )
        latency_ms = (time.perf_counter() - started) * 1000
        body = response.json() if response.status_code == 200 else {}
    except httpx.HTTPError as exc:
        return {"qid": example.qid, "status": 0, "latency_ms": 0.0, "error": str(exc)}

    record = {
        "qid": example.qid,
        "status": response.status_code,
        "latency_ms": round(latency_ms, 1),
        "server": body.get("timings_ms", {}),
        "repaired": body.get("repaired", False),
        "llm_calls": body.get("llm_calls", 0),
        "model_error": body.get("error"),
    }
    if response.status_code == 200:
        record["correct"] = score(example.db_id, example.gold_sql, body.get("sql")).correct
    return record


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:8000")
    parser.add_argument("-n", type=int, default=100)
    parser.add_argument("--concurrency", type=int, default=10)
    parser.add_argument("--label", default="run")
    args = parser.parse_args()

    health = httpx.get(f"{args.url}/health", timeout=30).json()
    examples = dataset.load_dev(limit=args.n)
    print(f"target : {args.url}  ({health['config']}, max_repairs={health['max_repairs']})")
    print(f"load   : {len(examples)} requests, {args.concurrency} concurrent\n")

    started = time.perf_counter()
    with ThreadPoolExecutor(max_workers=args.concurrency) as pool:
        records = list(pool.map(lambda ex: fire(args.url, ex), examples))
    wall = time.perf_counter() - started

    ok = [r for r in records if r["status"] == 200]
    lat = sorted(r["latency_ms"] for r in ok)
    breakdown = {
        key: sum(r["server"].get(key, 0.0) for r in ok) / max(1, len(ok))
        for key in ("total_ms", "llm_ms", "sql_ms", "other_ms")
    }
    summary = {
        "label": args.label,
        "requests": len(records),
        "concurrency": args.concurrency,
        "http_errors": len(records) - len(ok),
        "p50_ms": percentile(lat, 50),
        "p95_ms": percentile(lat, 95),
        "p99_ms": percentile(lat, 99),
        "max_ms": lat[-1] if lat else 0.0,
        "mean_ms": round(sum(lat) / max(1, len(lat)), 1),
        "throughput_rps": round(len(records) / wall, 2),
        "server_mean_ms": {k: round(v, 1) for k, v in breakdown.items()},
        "repaired": sum(r["repaired"] for r in ok),
        "no_working_query": sum(bool(r["model_error"]) for r in ok),
        "correct": sum(r.get("correct", False) for r in ok),
    }

    print(f"latency (client side, ms)  p50 {summary['p50_ms']:.0f}   p95 {summary['p95_ms']:.0f}"
          f"   p99 {summary['p99_ms']:.0f}   max {summary['max_ms']:.0f}   mean {summary['mean_ms']:.0f}")
    print(f"throughput                 {summary['throughput_rps']} req/s  ({wall:.1f}s wall)")
    b = summary["server_mean_ms"]
    print(f"server time, mean (ms)     total {b['total_ms']:.0f} = llm {b['llm_ms']:.0f}"
          f" + sql {b['sql_ms']:.0f} + other {b['other_ms']:.0f}")
    print(f"http errors {summary['http_errors']}   repaired {summary['repaired']}"
          f"   no working query {summary['no_working_query']}"
          f"   correct {summary['correct']}/{len(ok)}")

    out = config.RESULTS_DIR / f"load_{args.label}.json"
    out.write_text(json.dumps({"summary": summary, "requests": records}, indent=2),
                   encoding="utf-8")
    print(f"\nrecords: {out}")
    return 0 if not summary["http_errors"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
