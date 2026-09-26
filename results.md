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
| H5 | unit tests | `python -m pytest tests/ -q` | **75 / 75 pass** |

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

| # | configuration | tuning slice | full dev | vs. previous | McNemar p | LLM calls / q | p50 / p95 latency | cost / full run |
|---|---------------|--------------|----------|--------------|-----------|---------------|-------------------|-----------------|
| 1 | baseline — DDL + question | 74.0% | 72.0% (744/1034) | — | — | 1.000 | 0.74 / 1.24 s | $0.06 |
| 2 | + column instructions | 76.0% | 74.2% (767/1034) | +2.2% | 0.010 | 1.000 | 0.61 / 0.94 s | $0.06 |
| 3 | + sample rows & column values | 78.0% | 75.9% (785/1034) | +1.7% | 0.054 | 1.000 | 0.61 / 0.95 s | $0.17 |
| 4 | + 3 retrieved examples | - | 76.8% (794/1034) | +0.9% | 0.467 | 1.000 | 0.61 / 0.99 s | $0.24 |
| 5 | + repair on execution error | - | **79.0%** (817/1034) | **+2.2%** | **< 0.001** | 1.034 | 0.62 / 1.10 s | $0.25 |

Cumulative, baseline to run 5: **+7.1 points** (744 to 817 correct), 108
questions fixed against 35 broken, McNemar p < 0.001.

**Read the latency column with care.** It is the provider's own response time,
recorded when each response was generated and replayed from the cache, so it is
reproducible — but the rows were generated at different times of day under
whatever load the API had then. The baseline, with the *shortest* prompt, has
the *highest* latency, which only makes sense if time-of-run dominates. So
latency is not a controlled comparison between rows. The one increment that is
controlled is run 5's: its first attempts are run 4's cached responses, so the
p95 rise from 0.99 s to 1.10 s is the repair loop and nothing else.

How each error class moved across the stages - the most useful table here,
because the headline hides three trends running in different directions:

| configuration | accuracy | invalid SQL | wrong values | wrong column count | column permutation |
|---------------|----------|-------------|--------------|--------------------|--------------------|
| baseline | 72.0% | 11 | 232 | 46 | 51 |
| run 2 | 74.2% | 19 | 227 | 19 | 44 |
| run 3 | 75.9% | 19 | 202 | 26 | 46 |
| run 4 (k=3) | 76.8% | **33** | 184 | 22 | 45 |
| run 4 (k=5) | 77.8% | 33 | 177 | 20 | 45 |
| run 5 | 79.0% | **0** | 193 | 23 | 45 |

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

### Run 4 - retrieved few-shot examples

Three question/SQL pairs retrieved from the 7000-question training split and
put in the prompt as comments.

**What retrieval can and cannot carry here.** Spider's train and dev splits
share no databases - 140 against 20, no overlap - so a retrieved example's
table names are useless for the question being answered. All it can transfer is
the mapping from a question's shape to a query's shape. That decided the
retriever: TF-IDF over word 1- and 2-grams, **stop words deliberately kept**,
because "how many", "for each" and "list the" are the signal and the content
words - singer, country, stadium - are noise when the schema is different.

    Q: How many cartoons did each director create?
    -> How many movie reviews does each director get?
       SELECT count(*) , T1.director FROM Movie AS T1 JOIN ... GROUP BY T1.director

TF-IDF rather than embeddings on purpose: a few lines, no model to download, no
API call, and exactly reproducible. If it becomes the bottleneck that is a
measured reason to replace it, which is better than starting with the heavier
thing.

| configuration | full dev | vs. run 3 | McNemar p | cost |
|---------------|----------|-----------|-----------|------|
| run 3 | 75.9% | - | - | $0.171 |
| + 3 examples | **76.8%** | +0.9% | 0.467 | $0.239 |
| + 5 examples | 77.8% | +1.8% | 0.113 | $0.253 |

**k = 3 was declared before the runs, and k = 5 scored a point higher.** The
paired test between them puts that gap at p = 0.260 - no evidence either is
better. So k = 3 is carried forward: it is the pre-declared configuration and
it is cheaper, and where the accuracy difference is not established the cheaper
one wins. Reporting 77.8% instead would be picking the larger of two numbers
the data cannot separate, which is exactly the mistake the tuning slice taught
in run 2.

**The acceptance criterion for this stage failed.** It was set in advance:
46 questions still returned exactly the right data in the wrong column order,
instructions had not fixed it, and few-shot examples *demonstrate* a convention
instead of describing it. Result: **46 to 45**. One question.

Across four stages and three different mechanisms, column permutations have
gone 51 -> 44 -> 46 -> 45. This error class does not appear to be reachable
from the prompt at all, and the honest conclusion is that saying so is worth
more than another attempt at it.

The gain came from elsewhere: wrong values 202 to 184, wrong column count 26
to 22. And the churn is large - 65 questions fixed, 56 broken, for a net 9.

**Invalid SQL rose from 19 to 33.** 21 of the 25 new failures are `no such
column`, and they are all the same mistake:

    SELECT Model FROM cars_data     -- Model is on car_names, which is not joined
    SELECT T2.LName FROM Has_Pet AS T1 JOIN Pets AS T2   -- LName is on Student

The retrieved examples are often single-table queries and that shape transfers.
This is the *same* mechanism as run 2's breakage, where telling the model to
return fewer columns made it narrow the FROM clause with them: anything that
pushes toward simpler output costs joins.

One hypothesis this ruled out: the model is **not** copying table names from
the examples. Only 2 of the 25 new failures reference an identifier that
appears in a retrieved example but not in the target schema, and both are false
positives. The examples are labelled in the prompt as coming from other
databases, and that appears to have held.

**These 33 invalid queries are the input to the repair loop.** Every one fails
with a SQLite error naming the missing column. The error analysis gave the
repair loop a 2% share; four stages of prompt work have grown its addressable
set from 11 questions to 33.

### Run 5 - execute, and repair on error

Run the generated query. If SQLite rejects it, send the model the original
prompt, its failed query and the exact error message, and ask for a corrected
query. Cap: two repairs.

**The loop never sees the gold query.** A repair decision uses only the
predicted query's own execution result - did it error, did it return rows -
which is what a deployed system would know. `generate_with_repair` takes no
gold argument at all, and a test inspects its signature so that stays true.
Intermediate attempts are scored against gold only *after* the loop has
finished, to measure it; nothing flows back in.

Acceptance line, written before the run: **invalid SQL from 33 to under 10.**

| configuration | full dev | vs. run 4 | fixed | broken | McNemar p | calls / q | p95 latency |
|---------------|----------|-----------|-------|--------|-----------|-----------|-------------|
| run 4 | 76.8% | - | - | - | - | 1.000 | 0.99 s |
| + repair on error | **79.0%** | **+2.2%** | 23 | **0** | **< 0.001** | 1.034 | 1.10 s |
| + also repair empty results | 79.3% | +0.3% vs. above | 3 | 0 | 0.250 | 1.138 | 1.97 s |

**Invalid SQL went from 33 to 0.** All 33 triggered queries execute after
repair; 23 of them are now correct, 10 run but return the wrong rows. The
acceptance line was passed by a wide margin.

**23 fixed, 0 broken - the only stage in the ablation with no regressions, and
that is structural, not luck.** Every prompt stage changed the input for all
1034 questions, and each broke between 25 and 56 of them while fixing more.
Repair only touches a query that has already failed to execute, and a query
that fails to execute is always scored wrong. It can leave a question wrong or
make it right; it has no way to make a right answer wrong.

**Why cap the loop at two? The data says one is enough.** Correct after one
repair: 23 of 33. After two: still 23. Only 2 questions ever reached a second
repair; it made both queries valid, neither correct. Re-running with a cap of
one from the cache gives the identical 817 correct answers at 1.032 calls per
question instead of 1.034. The cap exists to bound the latency tail, not to buy
accuracy, and on this benchmark a single repair buys all of it.

**Repairing empty results: the risk did not materialise, and neither did the
gain.** The worry was the 49 dev questions whose gold answer is genuinely
empty: repairing an empty result pushes the model to change an answer that may
be right. The repair prompt allows for that - "if an empty result is genuinely
correct, repeat the query unchanged" - and it held. Of 41 first attempts that
were already correct and empty, **0 were broken**: 32 were repeated verbatim,
the other 9 rewritten but still correct. But of 13 wrong empty results, only
3 were fixed. +0.3 points, p = 0.250, for p95 latency going from 1.10 s to
1.97 s because 54 more questions now make a second round-trip. Not adopted.

**A prediction that failed.** When the extraction fallbacks in `generate.py`
never fired during the baseline, NOTES.md kept them on the grounds that repair
prompts are "exactly the prompt shape that tends to produce 'Sure - here's the
fix:' in front of the SQL". Across all 33 repair responses: **0 markdown
fences, 0 leading prose.** `gpt-4o-mini` followed the output instruction on
every repair too. The fallbacks remain tested and unexercised.

---

## Serving

`POST /query` (`src/api.py`) serves the run-5 configuration, with one change:
the repair cap is **1, not 2**. Run 5 measured both at the identical 817
correct answers; the second repair only lengthens the latency tail.

100 requests from the stratified dev slice, 10 concurrent, one uvicorn process
on a laptop, measured twice:

| | warm - responses cached | cold - every call live |
|---|---|---|
| p50 | **36 ms** | **666 ms** |
| p95 | 118 ms | 2,317 ms |
| p99 | 360 ms | 2,699 ms |
| throughput | 19.1 req/s | 9.9 req/s |
| server time: model | 6 ms | 849 ms |
| server time: SQL | 32 ms | 22 ms |
| server time: everything else | 15 ms | 6 ms |
| HTTP errors | 0 | 0 |
| correct | 75 / 100 | 75 / 100 |

The two columns answer different questions. **Cold is what a user waits**: an
uncached question costs about two thirds of a second at the median, and the
model is 97% of the server's time. **Warm is this code's own overhead** -
retrieval, prompt building, SQL execution, serialisation - with the model call
reduced to a disk read. Reporting only the warm number would claim a 36 ms
service that no real question ever gets.

Every response carries its own `timings_ms` breakdown. The client measures the
request end to end; the server attributes its share to the model, to SQL, and
to everything else, so a slow request can be blamed on the right thing.

**The service returns exactly the evaluated answers.** Every served answer is
scored against gold. Against the warm server, all 100 agree with the offline
evaluation of the same configuration - the code path that is deployed is the
code path that was measured.

**Temperature 0 is not deterministic, measured.** The cold server's live
answers differ in correctness from the cached ones on 2 of the 100 questions,
one in each direction - which is why both columns read 75. The cache, not the
temperature, is what makes every number in this file reproducible.

**The first load test measured itself.** Its first run reported p95 = 655 ms
for the warm server. The first 10 requests all took ~650 ms on the client while
the server's own timings for them were under 130 ms, and every request after
them took ~40 ms. The script was creating each thread's HTTP client inside the
timed region, and building an `httpx.Client` loads a CA bundle even for plain
http. Moving it outside the clock: p95 **655 ms to 118 ms**. The server-side
breakdown is what made the discrepancy visible.

**One heavy query, and a hypothesis it disproved.** The warm maximum, ~2.1 s,
is `wta_1#470`: the model wrote a `LEFT JOIN` of 20,662 players against the
rankings table and grouped by first name - 1.9 s on its own, against 0.42 s for
gold's inner join. I expected it to stall the other nine requests in flight.
It did not: the median of its batch was 47 ms, the same as any other. SQL runs
on a worker thread and Python's sqlite3 releases the GIL while SQLite works.

Not verified: the `Dockerfile`. Docker is not installed on the machine this was
built on, so the image has never been built or run.

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
