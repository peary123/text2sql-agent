# Text-to-SQL Agent with Execution-Feedback Repair

Ask a database a question in English; get SQL, run it, get rows back. Measured
on [Spider](https://yale-lily.github.io/spider) with execution accuracy, with an
ablation showing what each piece is actually worth.

No LangChain, no LlamaIndex — the prompt that goes to the model is built in this
repo and is readable verbatim in the response cache on disk.

---

## Why it's built this way

Generating plausible SQL is the easy half. The half that decides whether the
thing is usable is everything around it: running the query without letting a
model write to your database, noticing when it failed, and doing something about
it. So the measurement harness came first and the model came second.

That ordering paid for itself immediately — the first bug the evaluator caught
was in the harness, not the model ([NOTES.md](NOTES.md#the-row-cap-was-set-by-guessing-and-a-test-caught-it)).

## How the metric is validated

Execution accuracy is the right metric — string-matching SQL scores every valid
paraphrase wrong — but a metric you haven't tested is just a number generator.
So it gets checked in both directions across the whole dev set:

| pass | what it does | expected | measured |
|------|--------------|----------|----------|
| identity | score gold against itself | 100% | **100%** (1034/1034) |
| rewrite | wrap gold in `SELECT * FROM (...)`, renaming every column | ~100% | **100%** (803/803) |
| mutation | truncate gold's result with `LIMIT 1` | ~0% | **0%** (0/454) |

The rewrite pass fails if the metric is secretly comparing SQL text or column
names. The mutation pass fails if it's lenient enough to match anything.

```bash
python scripts/02_eval_gold.py
```

## How execution is sandboxed

The SQL is model-generated, so both of these are load-bearing.

**It cannot write.** The connection is read-only (`file:...?mode=ro`); a guard
independently rejects anything that isn't a single `SELECT`/`WITH`, which also
blocks `ATTACH` (a way to open a *second*, writable database around the
read-only handle) and `PRAGMA`; and Python's driver refuses multi-statement
strings, so `SELECT 1; DROP TABLE t` can't get through. The guard strips
comments first, quote-aware, so a `DROP` hidden behind `--` is still caught.

**It cannot hang.** A SQLite progress handler aborts the query once the deadline
passes — that's what actually stops a runaway cartesian product, since a Python
thread can't be killed from outside. The surrounding worker thread bounds how
long the harness waits for the parts SQLite doesn't step through. Results are
capped at 100k rows, because a *fast* query returning ten million rows exhausts
memory just as well as a slow one does.

## Results

| configuration | tuning slice (200) | full dev (1034) | calls / question | cost |
|---------------|-----------|-----------------|------------------|------|
| baseline — DDL + question | 74.0% | **72.0%** | 1.0 | $0.06 |

`gpt-4o-mini`, temperature 0. Where the 290 failures go:

| reason | count |
|--------|-------|
| wrong values returned | 232 |
| wrong number of columns | 46 |
| invalid SQL | 11 |
| right rows, wrong order | 1 |

Full breakdown, per-database accuracy and the per-question records are in
[results.md](results.md). Sample values, retrieved few-shot examples and the
repair loop aren't wired up yet — those rows fill in as they land.

Prompt iteration happens on a 200-question stratified slice covering all 20
databases; the full dev set is run once per configuration after that
configuration is frozen, so the headline number is not tuned against.

---

## Running it

```bash
pip install -r requirements.txt
```

```bash
python scripts/00_prepare_data.py
```

Downloads Spider 1.0 (~95 MB) into `data/`, which is gitignored — the dataset
isn't mine to redistribute.

```bash
python scripts/01_smoke_gold.py
```

```bash
python -m pytest tests/ -q
```

Tests need neither the dataset nor an API key.

For the generation stages, put your key in a `.env` file at the repo root
(it is gitignored) or export it, then check the whole setup end to end:

```bash
python scripts/03_check_setup.py
```

```bash
python scripts/04_run_baseline.py --limit 0
```

`LLM_MODEL` overrides the model (default `gpt-4o-mini`); a `claude-*` value
routes to Anthropic instead. `LLM_OFFLINE=1` runs entirely from cached
responses and errors on a cache miss, which is how the numbers in `results.md`
reproduce without spending anything.

## Layout

```
src/
  config.py     paths, .env loading
  dataset.py    loading dev / train questions, and the stratified tuning slice
  schema.py     schemas from tables.json and from sqlite_master
  execute.py    read-only, time-bounded SQL execution
  evaluate.py   execution-accuracy scoring
  generate.py   prompt construction and SQL extraction
  llm.py        provider calls + content-addressed disk cache
scripts/        runnable entry points, numbered in the order they're useful
tests/          37 tests; none need the dataset or an API key
results/        per-question records for every run in results.md
results.md      every run and the configuration that produced it
NOTES.md        decisions, and what went wrong
```

## Known limitations

- **Spider's databases are tiny** — at most 11 tables in the dev set. A real
  warehouse has hundreds and the schema won't fit in a prompt; that needs schema
  retrieval, which this doesn't attempt.
- **49 dev gold queries return an empty result set** (4.7%), so a wrong query
  that also returns nothing scores correct on those. Counted separately and
  reported next to the accuracy rather than hidden.
- **Ties under `ORDER BY ... LIMIT`** are resolved arbitrarily by SQLite. Two
  differently-written correct queries can pick different rows.
- **Numeric strings.** Five dev foreign keys join a TEXT column to a NUMBER one,
  so `'1'` and `1` can both be correct answers. Coercion is implemented but off
  by default, since it can only raise the score.

---

Spider 1.0 is CC BY-SA 4.0, Yale LILY Lab.
