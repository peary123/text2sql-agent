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

| # | configuration | tuning slice | full dev | vs. previous | McNemar p | calls / q | cost |
|---|---------------|--------------|----------|--------------|-----------|-----------|------|
| 1 | baseline — DDL + question | 74.0% | 72.0% (744/1034) | — | — | 1.0 | $0.062 |
| 2 | + column instructions | 76.0% | 74.2% (767/1034) | +2.2% | 0.010 | 1.0 | $0.064 |
| 3 | + sample rows & column values | 78.0% | **75.9%** (785/1034) | +1.7% | 0.054 | 1.0 | $0.171 |

Cumulative, baseline to run 3: **+4.0 points** (72.0% to 75.9%), McNemar
p < 0.001, 70 questions fixed against 29 broken.

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

### Run 2 — two sentences about columns

Targets the two largest categories in the error analysis: `column_order` (24%)
and `extra_column` (16%), 40% of failures between them. Both are one sentence
in the system prompt:

    Select only the columns the question asks for. Do not add an id, a count,
    or any other column that was not requested.
    List the selected columns in the order the question mentions them.

Each sentence was also run alone, so the pair can be attributed:

| configuration | tuning slice (200) | rank | full dev (1034) | rank | vs. baseline | McNemar p |
|---------------|--------------------|------|-----------------|------|--------------|-----------|
| baseline | 74.0% | — | 72.0% | — | — | — |
| + column order | 76.5% | 2 | 73.6% | 2 | +1.6% | 0.043 |
| + only requested columns | **77.0%** | **1** | 73.2% | **3** | +1.3% | 0.105 |
| + both | 76.0% | **3** | **74.2%** | **1** | **+2.2%** | **0.010** |

**The configuration that ranked last on the tuning slice ranked first on the
full dev set.** On 200 questions all three differences were inside the noise
(McNemar p = 0.11 to 0.34; 190 of 200 questions answered identically and carry
no information). Picking the slice winner would have selected
`only requested columns`, which on the full set is the weakest of the three and
does not reach significance.

The pair was chosen **before the full-dev runs**, on the grounds that the error
analysis had independently identified both categories and the slice gave no
evidence they interfere. Recording that here because it is the only thing that
makes "+2.2%, p = 0.010" a result rather than a search over four options.

Mechanism check — the instructions moved the error they were aimed at:

| evaluator reason | baseline | run 2 | change |
|------------------|----------|-------|--------|
| match | 744 | 767 | **+23** |
| column_count_mismatch | 46 | 19 | **−27** |
| value_mismatch | 232 | 227 | −5 |
| exec_error:sql_error | 11 | 19 | **+8** |
| order_mismatch | 1 | 2 | +1 |

48 questions fixed, 25 broken.

### Did each sentence move the error it aimed at?

Net accuracy hides this, so both target categories are counted directly. A
**column permutation** — the prediction holds exactly the right data and only
the column order differs — is detected exactly, by trying every reordering of
the predicted columns (`evaluate.is_column_permutation`, recorded per failure).
No sampling, no projection from hand labels.

| configuration | accuracy | permutation failures | wrong-column-count failures |
|---------------|----------|----------------------|------------------------------|
| baseline | 72.0% | 51 | 46 |
| + column order | 73.6% | **42** (−9) | 31 |
| + only requested columns | 73.2% | 48 (−3) | **22** (−24) |
| + both | 74.2% | 44 (−7) | **19** (−27) |

**One sentence worked and one mostly did not.**

- *Only requested columns* took its category from 46 to 19 — **59% recovered**.
- *Column order* took its category from 51 to 44 — **14% recovered**. Alone it
  managed 9 of 51, which is the best it ever did.

So most of the +2.2% came from one of the two sentences, and **44 questions
(4.3% of the dev set) still return exactly the right data in the wrong column
order**. Telling the model the convention in prose does not teach it the
convention. That is consistent with what the training split says: gold follows
the question's word order only 74% of the time, so there is no rule to state —
which makes this a retrieval problem rather than an instruction problem.
Retrieved few-shot examples show the expected column order *in context* rather
than describing it, and this category is the concrete thing to judge them on.

**The +8 invalid queries are a real cost, not rounding.** Constraining column
selection pushed the model to drop joins it still needed:

    SELECT Make, Year FROM cars_data WHERE Year = (SELECT MIN(Year) FROM cars_data)

`Make` lives on `car_names`, which is no longer joined. Told to return fewer
columns, the model narrowed the FROM clause with them. These 8 are exactly what
the execute-and-repair loop should recover, which makes run 2 a reason to expect
more from run 5 than its own error category suggests.

### Run 3 - showing the model what is in the columns

Targets `wrong_value` (12% of failures) and `wrong_column` (10%): a literal in
a WHERE clause that does not match what the column holds, and picking the
column that sounds right over the one that holds the value. Two additions,
appended to each CREATE TABLE as SQL comments:

    -- 3 example row(s):
    --   Id | Maker | FullName | Country
    --   1 | 'amc' | 'American Motor Company' | '1'
    -- Country holds: '1', '2', '3', '4', '5', '6', '7', '8'

That block alone answers two baseline failures: the model wrote
`WHERE Country = 'France'` against a column of country ids, and matched
`Maker` ('amc') against a full company name that lives in `FullName`.

**Values are only listed for low-cardinality columns.** The threshold is
measured, not guessed: across the 241 text columns in the 20 dev databases,
74% have 20 or fewer distinct values - continents, country codes, template
types, sexes - and those are exactly what a WHERE clause compares against. The
6.6% with more than 100 distinct values are names and addresses, where five
examples say nothing about the sixth. Cap is 20 distinct, 10 shown.

Values are rendered with `repr()`, not `str()`. `flight_2.airports.Country`
holds `'United States '` with a trailing space; a model that cannot see the
space writes a predicate that matches nothing and never finds out why.

| configuration | tuning slice | full dev | vs. run 2 | McNemar p | avg prompt | cost |
|---------------|--------------|----------|-----------|-----------|------------|------|
| run 2 | 76.0% | 74.2% | - | - | 338 tok | $0.064 |
| + sample rows | - | 75.2% | +1.1% | 0.242 | 653 tok | $0.150 |
| + column values | - | 75.1% | +1.0% | 0.260 | 640 tok | $0.137 |
| + both | 78.0% | **75.9%** | **+1.7%** | **0.054** | 955 tok | $0.171 |

**This row does not clear significance.** +1.7 points at p = 0.054 is
suggestive, not established, and it costs **2.7x the prompt tokens**. All three
variants point the same way, and the cumulative gain from baseline is solid
(+4.0 points, p < 0.001), which is why the row is kept - but in a system where
prompt size drives latency and cost, this is the first row to cut, and the
table should say so.

Closing the loop with the error analysis - of the 50 hand-labelled failures,
how many each run now answers correctly:

| hand label | n | baseline | run 2 | run 3 |
|------------|---|----------|-------|-------|
| column_order | 12 | 0 | 2 | **1** |
| extra_column | 8 | 0 | 5 | **4** |
| gold_debatable | 7 | 0 | 0 | 1 |
| wrong_value | 6 | 0 | 1 | **3** |
| wrong_column | 5 | 0 | 2 | **2** |
| query_logic | 4 | 0 | 0 | 0 |
| wrong_join | 3 | 0 | 1 | 2 |
| distinct | 2 | 0 | 2 | 2 |

`wrong_value` moved, which is the mechanism working: 1 of 6 to 3 of 6.
`wrong_column` did not move at all. `query_logic` has not moved since the
baseline and will not until something samples more than one candidate.

**The interesting number is the one that went backwards.** Run 2's categories
lost ground: `column_order` from 2 to 1, `extra_column` from 5 to 4. The exact
counters agree - column-permutation failures went from 44 to 46, and
wrong-column-count failures from 19 to 26. Tripling the prompt made run 2's two
sentences measurably less effective. The instructions did not change; what
changed is how much else is competing with them.

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

If that rate holds across all 290 failures, about **46 questions (4.5% of the
dev set)** can only be scored correct by reproducing an answer that does not
follow from the question — counting area-code rows when asked for states, and
so on. That is a *soft* ceiling near **95%**, not a hard one: a system tuned on
Spider's training split can learn these idiosyncrasies and clear it, which is
part of why published results with strong models and specialised pipelines
land around 84-86%. Either way the headroom above the 72% baseline is real and
large, and it is worth stating before reporting any improvement against it.
