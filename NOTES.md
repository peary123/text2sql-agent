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
