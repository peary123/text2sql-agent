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

50 of the 290 baseline failures, sampled proportionally across all 20 databases
and classified **by hand** — question, both queries and both result sets read
one at a time. Labels live in [`eval/error_labels.json`](eval/error_labels.json),
one primary cause each with a note naming the specific mistake; the sample with
its result previews is in [`results/error_sample.md`](results/error_sample.md).
`scripts/06_error_report.py` aggregates them.

| category | n | % | what it is evidence for |
|----------|---|---|-------------------------|
| column_order | 12 | 24% | instruction: order columns as the question names them |
| extra_column | 8 | 16% | instruction: return only the columns asked for |
| gold_debatable | 7 | 14% | nothing — the benchmark's answer is the questionable one |
| wrong_value | 6 | 12% | sample values in the schema prompt |
| wrong_column | 5 | 10% | sample values in the schema prompt |
| query_logic | 4 | 8% | self-consistency |
| wrong_join | 3 | 6% | foreign keys stated explicitly in the prompt |
| distinct | 2 | 4% | retrieved few-shot examples |
| ambiguous_question | 1 | 2% | nothing — the question has no single answer |
| sql_error | 1 | 2% | execute-and-repair loop |
| aggregation | 1 | 2% | retrieved few-shot examples |

**42 of 50 (84%) are the model's mistake. 8 (16%) are not.**

### Three things this changes

**1. Forty per cent of failures are about which columns come back, not about
understanding the question.** `column_order` and `extra_column` together are
20 of 50. In almost all of them the model found the right rows and then
returned them in a different order, or returned an extra count column nobody
asked for:

    Q:    How many cartoons did each director create?
    gold: SELECT count(*), Directed_by FROM cartoon GROUP BY Directed_by
    pred: SELECT Directed_by, COUNT(*) FROM Cartoon GROUP BY Directed_by

Same three rows, same numbers, scored wrong. Projected onto all 290 failures
that is ~116 questions, and the fix is two sentences in the prompt, not a
retrieval system.

**2. The evaluator's own reason codes cannot tell you what to fix.**
`value_mismatch` accounts for 80% of the machine-assigned failure reasons, and
it turns out to cover six different hand categories with six different fixes:

| hand label | value_mismatch | column_count_mismatch | exec_error |
|------------|---------------|----------------------|------------|
| column_order | 12 | | |
| extra_column | | 8 | |
| gold_debatable | 4 | 3 | |
| wrong_value | 6 | | |
| wrong_column | 5 | | |
| query_logic | 4 | | |
| wrong_join | 3 | | |
| distinct | 2 | | |
| sql_error | | | 1 |

This is the argument for reading fifty failures by hand rather than grouping by
error code and calling it analysis.

**3. There is a ceiling, and it is not 100%.** 16% of the sampled failures are
not the model's fault — either the question has no single answer, or Spider's
gold is the questionable one:

- `voter_1#687` — "How many states are there?" Gold counts area-code rows and
  answers **305**. The prediction's `COUNT(DISTINCT state)` answers **51**.
- `course_teach#387` — gold compares against a lowercased literal that matches
  nothing, so it returns all 7 teachers including the one the question excludes.
- `world_1#811` — gold's own `continent = "north america"` matches nothing on
  this database, so both aggregates come back `NULL`.
- `battle_death#493` — "List the name, date and result of each battle." Gold
  selects name and date.

If 16% of the remaining 290 failures are like this, the realistic ceiling for
this benchmark is around **76%**, not 100%. Worth stating before reporting any
improvement against it.
