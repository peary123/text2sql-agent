# Notes

Working notes: decisions I had to make and things that cost me time. Kept
because in six months I will not remember why any of this is the way it is.

---

## Decisions

### The evaluator came before the model

Nothing here can be judged without running the SQL, and until Spider's own gold
queries all execute, a low accuracy number is ambiguous — it could be the model
or it could be my plumbing. So the first thing that worked was a script that
runs Spider's answers, not mine.

All **1034 / 1034** dev gold queries execute cleanly. Everything that fails
from here belongs to generation.

### Execution accuracy, not string matching

There are many correct spellings of the same query — aliases, join order,
`COUNT(*)` vs `COUNT(id)`, `BETWEEN` vs two comparisons — and string comparison
scores all of them wrong. A prediction is correct when running it returns what
running the gold query returns.

The rules, and why:

- **Compare values, not column names.** `SELECT name` and
  `SELECT s.name AS singer` are the same answer.
- **Column order still matters.** Selecting the right two columns in the wrong
  order is a real error class, and tuple comparison catches it for free.
- **Row order matters only when the gold query asks for it.** "List the singers"
  shouldn't fail on ordering; "list them by age" should.
- **Rows are a multiset, not a set.** A missing `DISTINCT` is a real difference.
- **Numbers are rounded to 4 dp.** Float accumulation order inside `AVG` should
  not decide a benchmark.
- **A prediction that doesn't execute is wrong.** No partial credit.

### Detecting ORDER BY is not a substring search

`"order by" in sql.lower()` is wrong twice: it fires on text inside a string
literal (`WHERE note = 'order by date'`), and it fires on an ORDER BY inside a
subquery, which says nothing about the order of the final result. So the check
blanks out literals and comments and only looks at parenthesis depth zero.

When the parse is uncertain it returns False, which selects the *more forgiving*
multiset comparison. A metric should not fail a correct answer because the
harness misread the gold query.

### Validating the evaluator in both directions

Scoring gold against itself only proves the plumbing works — the strings are
identical, so of course they match. Two more passes over the whole dev set:

| pass | what it does | expected | measured |
|------|--------------|----------|----------|
| identity | gold scored against itself | 100% | **100%** (1034/1034) |
| rewrite | gold wrapped in `SELECT * FROM (...)`, plus a trailing comment | ~100% | **100%** (803/803) |
| mutation | same query truncated with `LIMIT 1` | ~0% | **0%** (0/454) |

The rewrite pass is what proves the metric isn't secretly comparing SQL text or
column headers — wrapping renames every output column. The mutation pass is what
proves it isn't so lenient that everything matches. The rewrite pass skips the
231 questions whose gold has a top-level ORDER BY, because SQLite does not
promise to preserve a subquery's ordering through a wrapper.

This is the answer to "how do you know your evaluation is right", and it is
worth more than the accuracy number it produces.

### Three layers between the model and the database

`DROP TABLE` from a language model is not hypothetical.

1. **Read-only connection** — `sqlite3.connect("file:...?mode=ro", uri=True)`.
   SQLite refuses any write. This is the layer that actually enforces safety.
2. **A leading-keyword guard** — only a single `SELECT`/`WITH` gets through.
   This is not redundant with layer 1: it blocks `ATTACH`, which could open a
   *second*, writable database and walk around the read-only handle, and it
   blocks `PRAGMA`.
3. **Single-statement enforcement**, free from Python's driver: `execute()`
   refuses a string containing more than one statement, so `SELECT 1; DROP ...`
   cannot get through even if the guard missed it.

The guard strips comments before reading the first keyword, because
`-- fetch rows\nDROP TABLE x` starts with a comment, not with `DROP`. The
stripper is quote-aware so a literal `'--'` survives. Both directions are
pinned in `tests/test_execute.py`.

### Timeouts need a progress handler *and* a thread

A model that forgets a join condition writes a cartesian product, and that does
not come back.

- `conn.set_progress_handler(cb, 10_000)` fires every 10k VDBE instructions and
  aborts the query when the deadline passes. **This is what actually stops the
  work.**
- The surrounding worker thread does *not* stop anything — Python threads can't
  be killed from outside. It bounds how long the harness *waits*, covering the
  time SQLite spends outside its bytecode loop.

Only the thread would leak a runaway query per timeout; only the handler leaves
the non-stepping cases unbounded.

### Everything the model says is cached, keyed by a hash of the request

The key is sha256 over provider, model, system prompt, user prompt, temperature
and max_tokens. Consequences:

- Re-running the evaluation to check a number costs nothing.
- Editing a prompt changes the key, so it correctly *misses* — no risk of
  reading a stale answer and concluding the edit did nothing.
- `LLM_OFFLINE=1` turns a cache miss into an error, which is how a published
  number can be shown to reproduce rather than be re-rolled.

`temperature=0` makes a model *mostly* deterministic, not deterministic. The
cache, not the temperature, is what makes the results reproducible.

Self-consistency deliberately samples at temperature > 0, where five identical
requests *should* differ — hence `cache_salt`, which puts the sample index in
the key so five samples get five entries instead of collapsing onto one.

Cache writes go to a temp file and are then `replace()`d, so Ctrl-C during a
long run can't leave a half-written JSON that poisons the next one. Entries are
sharded by the first two hex characters of the key; a flat directory of tens of
thousands of files is painfully slow to enumerate on Windows.

### No LangChain, no LlamaIndex

The prompt that goes out is built in this repo and is readable verbatim in the
cache file on disk. What's interesting here — what exactly is in the prompt,
what the repair loop feeds back, how retries are counted — is precisely what a
framework hides.

### The baseline prompt is deliberately plain

Schema as the database's own `CREATE TABLE` statements, then the question. No
examples, no sample values, no retries.

The DDL rather than a hand-written schema summary for three reasons: it is a
format the model has seen an enormous amount of; it carries types, primary keys
and foreign keys in one place; and it is read from the database the query will
actually run against, so it cannot drift out of sync with it.

Keeping the baseline plain is not laziness — it is the number every later
improvement is measured against. A baseline that already has half the tricks
folded into it makes the ablation look smaller than it is, and there is no way
to recover the missing rows afterwards without re-running everything.

### Only retry what can succeed on a retry

The first live call failed with a `TypeError` from inside a dependency, and the
retry loop dutifully tried it four more times with exponential backoff — seven
seconds to report an error that was never going to change, with the real
exception buried under a wrapper.

Now `_is_retryable` gates it: retry on connection errors, timeouts, and HTTP
408/409/425/429/5xx; re-raise everything else immediately with its original
traceback. A bad key, a malformed request, or a broken dependency should fail
in milliseconds and say what actually happened.

---

## Things that bit me

### The row cap was set by guessing, and a test caught it

I capped results at 10,000 rows so that a query which is *fast* but returns ten
million rows can't exhaust memory — the timeout doesn't catch that one.

Then the identity pass came back **1032/1034** instead of 100%. Both failures
were the same question asked twice:

    SELECT first_name, last_name FROM players ORDER BY birth_date   -- wta_1

`wta_1.players` has 20,662 rows, so gold hit my cap, got flagged `truncated`,
and the evaluator correctly refused to judge a result it had only seen a prefix
of. The metric was right; my constant was wrong.

Measured the actual distribution instead of guessing again: the largest
legitimate dev result is those 20,662 rows, the next largest is 2,217. Cap is
now 100k — ~5x headroom over anything the benchmark asks for, still only tens of
megabytes of memory. Identity went to 1034/1034.

Worth keeping because it's the whole argument for building the evaluator first:
the first thing it caught was a bug in my own harness.

### One Spider database is not valid UTF-8

`sqlite3`'s default text factory decodes strictly and raises mid-fetch:

    wta_1.players.last_name: OperationalError: Could not decode to UTF-8
                             column 'last_name' with text 'Treyes Albarrac??N'

`wta_1` carries **62 of the 1034 dev questions (6%)**, so with a strict factory
those could all have failed and looked like model errors. Fix:

    conn.text_factory = lambda b: b.decode("utf-8", errors="replace")

Applied identically to prediction and gold, so the comparison stays fair.

### `Path.as_uri()`, not an f-string, for the SQLite URI

`f"file:{path}?mode=ro"` works on Linux and silently breaks on Windows:
`file:C:\Users\...` is read as a *relative* path whose first component is `C:`,
and backslashes and spaces aren't URI-legal. `Path.as_uri()` emits
`file:///C:/Users/...` percent-encoded, and `?mode=ro` appends cleanly.

### Windows console encoding

Printing a Spider row died with `UnicodeEncodeError: 'gbk' codec can't encode
character '\ufffd'` — the console defaults to a legacy codepage. Entry-point
scripts now call `sys.stdout.reconfigure(encoding="utf-8", errors="replace")`.
Nothing to do with the model; cost me twenty minutes.

### 49 gold queries return an empty result set

Out of 1034 — 4.7%. Under execution accuracy, *any* wrong query that also
returns nothing scores correct on those. The standard metric doesn't special-case
it and neither do I, but the evaluator counts them separately and the number is
reported next to the accuracy so the ablation deltas can be read honestly.

### Spider's own schemas are internally inconsistent

Five foreign keys in the dev databases join columns of different declared types:

    car_1                     car_makers.Country (text)     -> countries.CountryId (number)
    concert_singer            concert.Stadium_ID (text)     -> stadium.Stadium_ID (number)
    concert_singer            singer_in_concert.Singer_ID   -> singer.Singer_ID
    employee_hire_evaluation  evaluation.Employee_ID (text) -> employee.Employee_ID (number)
    museum_visit              visit.visitor_ID (text)       -> visitor.ID (number)

SQLite's dynamic typing makes the joins work anyway, but it means the same value
can come back as `'1'` from one query and `1` from another, and two semantically
identical queries can then compare unequal. `normalize_value` has a
`coerce_numeric_strings` flag for this, **off by default** — coercion can only
ever raise the score, so it stays an explicit reported choice rather than a
silent one. Whether it actually matters is a question for the error analysis,
once there are real predictions to look at.

### Every API response died in the HTTP layer before we saw it

The first real call failed with:

    TypeError: Decompressor.decompress() got an unexpected keyword argument
               'output_buffer_limit'

Not a key problem, not a model problem — the response never reached our code.
The provider SDK bundles its own HTTP stack, whose brotli decoder calls
`decompress(data, output_buffer_limit=...)`, while the installed `brotlicffi`
exposes `decompress(data)`. `brotlicffi 1.1.0.0` is the latest release, so
there is no version to upgrade to; the mismatch is in the HTTP library.

Fix: ask the API not to brotli-compress at all —
`Accept-Encoding: gzip, deflate` on both clients. Chosen over uninstalling the
brotli bindings because that would have meant changing a shared Anaconda
environment other projects depend on, and over pinning versions because there
is no working combination to pin to. It costs a little bandwidth on JSON
payloads of a few kilobytes, and it means anyone who clones this repo gets a
working client regardless of which brotli bindings they happen to have.

Worth remembering as the general shape of the problem: "the model is broken"
and "the HTTP client cannot decode the response" look identical from the
outside. The traceback was the whole answer, and the retry loop was hiding it.

### The extraction fallbacks never fired

`extract_sql` handles markdown fences, leading prose and trailing explanation,
with eleven tests covering them. Across all 1034 baseline questions,
**0 responses used a fence and 0 were unparseable** — `gpt-4o-mini` obeyed the
"nothing else" instruction every time.

Keeping the code and the tests anyway, because the repair loop feeds error
messages back and asks for a corrected query, which is exactly the prompt shape
that tends to produce "Sure — here's the fix:" in front of the SQL. Noted here
so the honest version gets told: this robustness is currently untested against
real traffic, not proven by it.

### The tuning slice was four databases pretending to be twenty

`dev.json` is ordered by database, so the obvious tuning slice —
`examples[:200]` — covers **4 of the 20 dev databases**, and 92 of its 200
questions are `car_1`, which turns out to be the hardest database in the set.

    dev[:200]   64.5%
    dev[200:]   73.7%
    full dev    72.0%

Seven and a half points below the full set. Every prompt change would have been
judged almost entirely on `car_1`, with nothing said about whether the gain
transferred to the other sixteen databases — and the ablation table is the whole
point of the project.

Replaced with a proportional stratified sample: each database contributes its
share of the 200, with a floor of one so no database drops out. It scores
**74.0%**, two points from the full set instead of seven and a half.

Deterministic on purpose — evenly spaced indices within each database rather
than a random draw. Two configurations have to be compared on *identical*
questions, or the difference between them includes the difference between two
samples. It also means the slice is the same on every machine, with no seed to
remember.

Caught by comparing the slice against the rest of the set rather than by
thinking about it in advance. Worth doing for any held-out split whose ordering
you did not choose yourself.

### I read twelve failures, found a pattern, and the pattern was not there

The biggest error category is column order: the model returns the right rows
with the columns in a different order from gold. Across the twelve I had read,
gold put the aggregate first every single time —

    Q:    How many cartoons did each director create?
    gold: SELECT count(*), Directed_by FROM cartoon GROUP BY Directed_by
    pred: SELECT Directed_by, COUNT(*) FROM Cartoon GROUP BY Directed_by

— so the obvious conclusion was that Spider has an aggregate-first convention
and few-shot examples would teach it. I checked that against all 7000 training
queries before writing it down:

    two-column SELECTs with exactly one aggregate: 633
      aggregate first   199  (31.4%)
      group key first   434  (68.6%)

The opposite. There is no aggregate-first convention; if anything the
convention runs the other way.

The mistake was reading a pattern off a sample that is *conditioned on
disagreement*. Those twelve failures are exactly the questions where the model
— which defaults to group-key-first — disagreed with gold. Gold being
aggregate-first in all of them is guaranteed by how they were selected, and
says nothing about the population. Any error sample has this property: it is
the set of cases where two things differed, so it over-represents whatever
direction the difference runs in.

What does hold, tested the same way: gold's column order follows the order the
question names things in **73.8%** of decidable training cases. So the fix is an
instruction — "return the columns in the order the question mentions them" —
and it has a measurable ceiling of about three quarters of the category rather
than all of it. That is a much less exciting claim than the first one, and it
is the one supported by the data.

Rule I am keeping: a pattern noticed in a hand-read sample is a hypothesis.
Test it against the full data before it is allowed to justify a change.

### The evaluator's error codes are not an error analysis

`value_mismatch` is 80% of the baseline's machine-assigned failure reasons. It
turned out to cover six hand categories with six different fixes — a mis-cased
string literal, a wrong join path, an inverted negation, a missing DISTINCT, a
column-order difference, and a case where Spider's own answer is wrong.

Grouping by error code and calling it analysis would have produced one bucket
labelled "the values were different", which is true and useless. The fifty
hand labels are what turn 290 failures into a list of things to build.

### The tuning slice ranked the configurations backwards

Three prompt variants, measured on the 200-question slice first:

    + column order              76.5%   (2nd)
    + only requested columns    77.0%   (1st)
    + both                      76.0%   (3rd)

Then the same three on the full dev set:

    + column order              73.6%   (2nd)
    + only requested columns    73.2%   (3rd)
    + both                      74.2%   (1st)

The slice's winner is the full set's loser, and its loser is the winner.

Nothing went wrong — the slice was never able to rank them. Of its 200
questions, 190 were answered identically by every variant and carry no
information at all; the ranking rested on 9 to 11 flipped questions.
McNemar put every slice comparison at p = 0.11 to 0.34. A one-question gap
between two configurations is a coin toss being read as a result.

Two things follow, and I am keeping both:

**Use a paired test, not two accuracy figures.** Both runs answer the same
questions, so the informative unit is the *disagreement*: 48 fixed, 25 broken.
Comparing 72.0% against 74.2% as if they were independent samples throws away
the pairing and needs far more data for the same confidence.

**Decide the configuration before running the thing you will report.** The pair
was chosen on the error analysis — it had independently found both categories,
and the slice showed no evidence of interference — and written down before any
full-dev run. Had I picked the slice winner instead, I would have shipped
`only requested columns`: +1.3% on the full set, p = 0.105, not significant.
"+2.2%, p = 0.010" is a result; the same number picked out of four options
after the fact is a search.

### An instruction that helps can also break things

Run 2 fixed 48 questions and broke 25. The breakage is not noise, and it has a
mechanism:

    -- "select only the columns the question asks for"
    SELECT Make, Year FROM cars_data WHERE Year = (SELECT MIN(Year) FROM cars_data)

`Make` is on `car_names`. Told to return fewer columns, the model narrowed the
FROM clause along with them and dropped a join it still needed. Invalid SQL
went from 11 to 19 questions while the error actually targeted,
`column_count_mismatch`, went from 46 to 19.

Reporting the net (+2.2%) without the decomposition would have hidden a
regression inside an improvement. It also predicts something: those 8 broken
queries fail with a SQLite error message, which is exactly the input the
execute-and-repair loop takes. The cost of this change is recoverable by a
later one, and that is only visible because the breakdown was kept.

### Measure the target category, not just the headline

Run 2 added two sentences and accuracy went from 72.0% to 74.2%. That number
says the change was worth keeping. It does not say *which* sentence earned it,
and averaged over two categories it hid the fact that one of them barely moved.

Column permutations can be counted exactly — try every reordering of the
predicted columns and see if any of them matches gold — so there is no excuse
for projecting that category from a 50-question sample. Counted exactly:

    configuration              accuracy   permutations   wrong column count
    baseline                     72.0%         51               46
    + column order               73.6%         42               31
    + only requested columns     73.2%         48               22
    + both                       74.2%         44               19

"Only requested columns" recovered 59% of its category. "Column order"
recovered 14%. Most of the +2.2% came from one sentence.

This also explains itself: gold follows the question's word order only 74% of
the time, so there is no rule to state, and stating the tendency in prose did
not teach it. Column order looks like a retrieval problem — a few-shot example
*shows* the expected order instead of describing it — which gives the next run
a specific prediction to be judged against rather than a vague hope of
improvement.

The general point: an ablation row should carry the count of the error it was
aimed at, not only the headline. Otherwise a change that half works and a change
that fully works look identical.

### An arithmetic slip worth keeping written down

I recorded the ceiling as "around 76%" after finding that 16% of sampled
failures are not the model's mistake. That is wrong: 16% *of the 290 failures*
is about 46 questions, which is 4.5% of the 1034-question dev set, so the
ceiling is near **95%**, not 76%.

Confusing "share of failures" with "share of the whole" made the remaining
headroom look like 4 points when it is more than 20. Published Spider dev
results with strong models sit around 84-86%, which is the number worth
measuring against.

### Which columns are worth showing values for, measured

Listing a column's values only teaches the model something when the values are
a closed set. Rather than guess a threshold, I counted the 241 text columns
across the 20 dev databases:

    distinct values     share of text columns
    1-5                        29.5%
    6-20                       44.8%      <- 74.3% together
    21-100                      8.7%
    >100                        6.6%

Three quarters of text columns hold 20 or fewer distinct values, and those are
exactly the columns a WHERE clause compares a literal against: continents,
country codes, template types, sexes. The 6.6% above 100 are surnames and
addresses, where five examples say nothing about the sixth and only cost
tokens. Threshold: enumerate at 20 or fewer, show at most 10.

Two specific baseline failures this answers outright:

    car_1.car_makers.Country   holds '1','2','3'...   (ids, not country names)
    Ref_Template_Types.Code    holds 'AD','BK','PPT'  ('PPT' is a code)

The model had written `WHERE Country = 'France'` against the first and looked
'PPT' up as a description against the second.

### repr(), not str(), for values in a prompt

`flight_2.airports.Country` holds `'United States '` - with a trailing space.
Rendered with `str()` the model sees `United States`, writes the obvious
predicate, matches zero rows, and has no way to find out why. `repr()` keeps
the quotes and makes whitespace visible.

Small thing, and the sort of thing that only shows up if you read the data
rather than the schema.

### A longer prompt made the earlier instructions worse

Run 3 tripled the prompt. Accuracy went up, but run 2's two sentences got
measurably weaker:

                              run 2   run 3
    column-permutation fails     44      46
    wrong-column-count fails     19      26
    hand-labelled column_order  2/12    1/12
    hand-labelled extra_column   5/8     4/8

The instructions are byte-identical in both runs. What changed is how much else
is competing with them - roughly 340 tokens of schema became roughly 955.

So the ablation is not as additive as the table's layout implies. Each row is a
delta against the row above it, which is the right way to attribute a change,
but it quietly assumes the earlier changes keep working at full strength. Here
one measurably did not, and the only reason I can say so is that the target
error of each row is counted separately from the headline.

### A row that does not clear significance, kept anyway

Run 3 is +1.7 points at McNemar p = 0.054, for 2.7x the prompt tokens. That is
suggestive, not established.

Kept, for two reasons: all three variants of it move the same direction, and
the cumulative gain from baseline is unambiguous (+4.0 points, p < 0.001). But
the row says p = 0.054 and 2.7x cost in the table, because a reader deciding
whether to adopt this needs both numbers, and in a system where prompt size
drives latency and bill it is the first thing to cut.

Deleting a marginal row would make the table look better and be worth less.

### Stop words are the signal when the schemas do not match

Spider's train and dev splits share no databases: 140 against 20, no overlap.
A retrieved example's table names are therefore useless for the question being
answered - the model cannot reuse them. The only thing an example can carry
across is the mapping from a question's shape to a query's shape.

That inverts the usual retrieval setup. Standard text retrieval strips "how
many", "for each", "list the" as stop words and keeps the content words. Here
the content words - singer, country, stadium - are the noise, because the
schema behind them is not the one being queried, and the stop words are the
whole signal. So: word 1- and 2-grams, `stop_words=None`.

    Q: How many cartoons did each director create?
    -> How many movie reviews does each director get?
       SELECT count(*) , T1.director ... GROUP BY T1.director

The retrieved example demonstrates the column order that gold uses. It just
turned out not to be enough - see below.

### The acceptance criterion failed, and that is the result

Before run 4 I wrote down what would count: 46 questions returned exactly the
right data in the wrong column order, instructions had not fixed it, and
few-shot examples demonstrate a convention rather than describing it. If
demonstration works, that number should fall.

    51 -> 44 -> 46 -> 45
    baseline  run 2  run 3  run 4

One question, across four stages and three mechanisms. The accuracy still went
up, so without the per-category counter this stage would have been written up
as a success for a reason that is not true.

The honest conclusion is that this error class is not reachable from the
prompt. Column order in Spider follows the question's word order only 74% of
the time, so there is no rule to state and no consistent convention to
demonstrate - the retrieved examples themselves disagree with each other,
because the training data does. Saying that, with the counts behind it, is
worth more than a fifth attempt.

Setting the criterion in advance is what makes this reportable. Afterwards,
"+0.9% overall" and "the thing I was aiming at did not move" are equally true,
and only one of them gets written down if nobody decided beforehand which one
was the question.

### Everything that simplifies the output costs joins

Invalid SQL across the four stages: 11, 19, 19, 33.

    run 2:  told to return fewer columns
            SELECT Make, Year FROM cars_data   -- Make is on car_names
    run 4:  shown mostly single-table examples
            SELECT Model FROM cars_data        -- Model is on car_names

21 of run 4's 25 new failures are `no such column`, and they are this. Two
different changes, made for two different reasons, producing the same failure:
push the model toward simpler output and it drops the join along with the
complexity.

A hypothesis this ruled out: the model is *not* copying table names from the
retrieved examples. Only 2 of the 25 reference an identifier present in an
example but absent from the target schema, and both are false positives. The
prompt labels the examples as coming from other databases and that held.

Worth noticing the shape of it: four stages of prompt work have grown the
repair loop's addressable set from 11 questions to 33, all of them failing with
an explicit SQLite error that names the missing column. The error analysis
allotted the repair loop 2%. The ablation built it a bigger job than that.

### The repair loop cannot see the answer key, by construction

The easy way to get a spectacular repair number is to let the loop compare its
result against gold and retry until they match. That is not a text-to-SQL
system; it is a search against the answer key.

So the loop's only input is the predicted query's own execution result - did
SQLite reject it, did it return rows - which is exactly what a deployed system
would know. `generate_with_repair` has no parameter a gold query could arrive
through, and `test_repair_loop_cannot_receive_the_gold_query` inspects the
signatures of every function in the loop to keep it that way. Being careful is
not a guarantee; an interface that cannot express the mistake is.

Intermediate attempts *are* scored against gold, but only after the loop has
finished, to measure what it did.

### Why the repair stage broke nothing when every other stage did

    stage       fixed   broken
    run 2         48      25
    run 3         48      30
    run 4         65      56
    run 5         23       0

Not luck. Each prompt stage changes the input for all 1034 questions, so it
moves answers in both directions and the table only sees the net. Repair
touches only a query that has already failed to execute, and a query that fails
to execute is always scored wrong. It can leave a question wrong or make it
right. It has no path to making a right answer wrong.

That is the argument for putting a repair loop behind any prompt change: its
downside is bounded by construction, which none of the prompt changes' are.

### "Why cap repairs at two?" - measured, not asserted

The plan fixed the cap at two, and it is a standard interview question. The
answer turned out to be that it should be one:

    correct after 0 repairs    0 / 33
    correct after 1 repair    23 / 33
    correct after 2 repairs   23 / 33

Only two questions ever reached a second repair. It made both queries valid and
neither correct. Re-running with a cap of one, entirely from the cache, gives
the identical 817 correct answers at 1.032 calls per question instead of 1.034.

So the honest version: a cap exists to bound the latency tail, since each
repair is a full round-trip with the whole prompt. On this benchmark one repair
captures all of the accuracy. If the error message names the missing column
and the model still gets it wrong, a second look at the same message rarely
changes its mind.

### The escape hatch for correct empty answers held

Repairing queries that return no rows was the risky variant: 49 dev gold
answers are genuinely empty, and "your query returned nothing, fix it" pushes
the model to change an answer that may be right. The repair prompt allowed for
that explicitly - if an empty result is genuinely correct, repeat the query
unchanged.

    first attempt empty and already correct:   41
      repeated verbatim                        32
      rewritten, still correct                  9
      broken                                    0

    first attempt empty and wrong:             13
      fixed                                     3

The safety worked. The gain did not: +0.3 points, p = 0.250, while p95 latency
went from 1.10 s to 1.97 s because 54 more questions now make a second
round-trip. Not adopted - a variant that is safe and nearly useless is still
nearly useless.

### A prediction of mine that failed

When the baseline never exercised the SQL-extraction fallbacks, I kept them on
the reasoning that repair prompts would produce "Sure - here's the fix:" in
front of the SQL. Across all 33 repair responses: 0 markdown fences, 0 leading
prose. The fallbacks are still tested and still unexercised by this model.
Writing the prediction down beforehand is the only reason there is anything to
report here.

### Latency across rows is not a controlled comparison

Latency is the provider's own response time, recorded at generation and
replayed from the cache, so it reproduces exactly. But:

    baseline   prompt ~340 tokens   p50 0.74 s
    run 3      prompt ~955 tokens   p50 0.61 s

The shortest prompt has the slowest responses. The rows were generated at
different times of day, and API load evidently outweighs a 3x difference in
input length. So the latency column cannot rank configurations. The one
increment that is controlled is the repair loop's, because its first attempts
are run 4's cached responses: p95 0.99 s to 1.10 s is the loop and nothing
else. Serving-latency claims belong to the FastAPI stage, measured under a
fixed load in one sitting.

### Two latency numbers, because they answer two questions

Every dev question is already in the response cache, so the obvious load test -
fire dev questions at the server - measures a server that never calls the
model. It reports p50 = 36 ms, which is true and describes nothing a user
experiences.

So the load test runs twice. Warm: the normal cache, which isolates this code's
own overhead. Cold: `serve.py --cold` points the cache at an empty temporary
directory, so every call is live. That is the number a real question gets -
p50 666 ms - and the model is 97% of it.

Each response also reports its own time split into model, SQL and everything
else. Without that breakdown, the next paragraph would have ended in the wrong
place.

### The load test was measuring itself

First warm run: p95 = 655 ms. Looked at by position, the first 10 requests all
took ~650 ms on the client and every request after them took ~40 ms - and the
server's own timings for those first 10 were under 130 ms.

When the client is slow and the server is not, the time is in the client. Each
worker thread created its `httpx.Client` on first use, inside the timed region,
and building one loads a CA bundle even for plain http. Moved outside the
clock: p95 655 ms to 118 ms.

The server-side breakdown is the only reason this was caught. With a single
end-to-end number, 655 ms would have gone into the README as a property of the
service.

### A heavy query did not block the others - I guessed wrong

The warm run's maximum is `wta_1#470`: the model wrote a `LEFT JOIN` of 20,662
players against rankings, grouped by first name - 1.9 s on its own, where
gold's inner join takes 0.42 s. I assumed it would stall the other requests in
flight on a single-process server. The median of its batch of ten was 47 ms,
like any other batch. Each SQL query runs on its own worker thread, and
Python's sqlite3 releases the GIL while SQLite executes. Checked before writing
the fix I was about to write.

### Temperature 0 is not deterministic - now with a number

The cold server re-asked 100 questions live that the cache had answered before.
Correctness differed on 2 of them, one in each direction, so both runs read
75/100 and the aggregate hides it. That is the measured version of what this
file has claimed since the first day: the cache, not the temperature, is what
makes the results reproducible.

### Serving choices the ablation made

- **One repair, not two.** Run 5 measured both at the identical 817 correct
  answers. The service takes the cheaper one; the ablation keeps the
  pre-declared one. Recording both is what lets them differ honestly.
- **`def`, not `async def`, for `/query`.** The model call and SQLite are both
  blocking. FastAPI runs sync endpoints on a thread pool; an `async def` would
  run those blocking calls on the event loop and serialise every request behind
  the slowest.
- **`db_id` is an allowlist lookup, never a path component.** It becomes
  `data/<db_id>/<db_id>.sqlite`, so an unchecked `../../x` walks out of the data
  directory.
- **A prompt-injected `DROP TABLE` is a test, not a hope.** The model's output
  is untrusted input to the executor; the executor, not the model, is what
  enforces read-only. The test counts the table's rows afterwards.
