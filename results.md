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
| H5 | unit tests | `tests/test_execute.py`, `tests/test_evaluate.py` | **20 / 20 pass** |

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

One variable at a time. Prompt iteration happens on `dev[:200]` only; the full
1034-question dev set is run once per configuration, after the configuration is
frozen, so the headline number is not tuned against.

_No model runs yet._

---

## Error analysis

_Pending the baseline run._
