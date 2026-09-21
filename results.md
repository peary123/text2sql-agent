# Results

Append-only log of what was run and what it produced. Superseded numbers stay
where they are so the deltas can be audited.

Model: `gpt-4o-mini` · temperature 0 · every response cached under `.llm_cache/`,
so `LLM_OFFLINE=1` reproduces any number below without an API call.

---

## Harness validation

Before any model was involved. The evaluator decides every number in this file,
so it is checked in both directions — one pass that should score everything
correct, one that should score everything wrong.

| run | check | command | result |
|-----|-------|---------|--------|
| H1 | gold SQL executes, full dev set | `scripts/01_smoke_gold.py --limit 0` | **1034 / 1034**, 49 empty result sets |
| H2 | gold scored against itself | `scripts/02_eval_gold.py --mode identity` | **100.0%** (1034/1034) |
| H3 | gold vs. a semantics-preserving rewrite | `scripts/02_eval_gold.py --mode rewrite` | **100.0%** (803/803) |
| H4 | gold vs. a meaning-changing mutation | `scripts/02_eval_gold.py --mode mutation` | **0.0%** (0/454) |
| H5 | unit tests | `python -m pytest tests/ -q` | **37 / 37 pass** |

H2 is the ceiling this project can be measured against: every dev question has a
gold query that runs, so any later failure belongs to generation.

H3 wraps each gold query in `SELECT * FROM (...)`, which renames every output
column — it fails if the metric is secretly comparing SQL text or column
headers. It skips the 231 questions with a top-level `ORDER BY`, since SQLite
does not promise to preserve a subquery's ordering through a wrapper.

H4 truncates each gold result to one row. It fails if the metric is lenient
enough to match anything. Restricted to the 454 questions whose gold returns 2+
rows, where the answer is guaranteed to change.

**H2 initially came back 1032/1034.** Both failures were `wta_1` queries
returning 20,662 rows against a 10,000-row result cap I had set by guessing.
Cap re-derived from the data (largest legitimate dev result 20,662, next largest
2,217) and raised to 100k. Details in `NOTES.md`.

Dataset as loaded: 1034 dev questions over 20 databases; largest dev schema is
`student_transcripts_tracking` at 11 tables / 56 columns. Few-shot pool is
`train_spider.json` (7000 questions), disjoint from dev.

---

## Execution accuracy

One variable at a time. Prompt iteration happens on a 200-question tuning
slice; the full 1034-question dev set is run once per configuration, after the
configuration is frozen, so the headline number is not tuned against.

The slice is a **proportional stratified sample**, not the first 200 questions.
dev.json is ordered by database, so `examples[:200]` covers 4 of 20 databases
and is 92/200 `car_1` — the hardest one. It scores 64.5% against the full set's
72.0%, and tuning on it would have been tuning on `car_1`. The stratified slice
covers all 20 databases with each one's share within 0.5% of its share of the
full set, and scores 74.0% — a 2.0-point gap instead of 7.5.

| # | configuration | tuning slice | full dev | LLM calls / question | cost | wall clock |
|---|---------------|-----------|----------|----------------------|------|------------|
| 1 | baseline — DDL + question | 74.0% | **72.0%** (744/1034) | 1.0 | $0.062 | 84 s |

Per-question records: [`results/baseline_full.jsonl`](results/baseline_full.jsonl).
Re-runs are free and byte-identical — `--offline` serves the whole run from the
response cache and errors on a miss.

### Run 1 — baseline

`gpt-4o-mini`, temperature 0, one call per question. The prompt is the
database's own `CREATE TABLE` statements followed by the question; no examples,
no sample values, no retries. Deliberately plain, because it is the number
every later improvement is measured against.

Failure breakdown, straight from the evaluator:

| reason | count | share of all questions |
|--------|-------|------------------------|
| match | 744 | 72.0% |
| value_mismatch | 232 | 22.4% |
| column_count_mismatch | 46 | 4.4% |
| exec_error:sql_error | 11 | 1.1% |
| order_mismatch | 1 | 0.1% |

Two things worth noting before reading too much into that: 42 of the 744
correct answers are questions whose gold result is empty, and
`column_count_mismatch` counts returning the wrong *number* of columns, which
is a different mistake from returning the wrong values.

Accuracy varies by database far more than the aggregate suggests:

| database | accuracy | | database | accuracy |
|----------|----------|---|----------|----------|
| car_1 | 46.7% (43/92) | | poker_player | 97.5% (39/40) |
| real_estate_properties | 50.0% (2/4) | | orchestra | 95.0% (38/40) |
| world_1 | 55.0% (66/120) | | museum_visit | 88.9% (16/18) |
| student_transcripts_tracking | 56.4% (44/78) | | | |
| wta_1 | 62.9% (39/62) | | | |

`car_1` and `world_1` alone account for 103 of the 290 failures.

Model behaviour: **0 responses were unparseable** and **0 used markdown
fences** — the "reply with SQL and nothing else" instruction held for every one
of the 1034 questions, so none of the extraction fallbacks in `generate.py`
were exercised on this run.

---

## Error analysis

_Next: hand-classify 50 of the 290 failures._
