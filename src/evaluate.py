"""Execution accuracy: does the predicted query return what the gold query returns?

String-matching SQL is the wrong metric. There are many correct spellings of the
same query -- table aliases, join order, `COUNT(*)` versus `COUNT(id)`,
`BETWEEN` versus two comparisons -- and a string comparison scores all of them
wrong. So a prediction counts as correct when running it produces the same
result set as running the gold query on the same database.

The comparison rules, and why each one is what it is:

* **Rows are compared as tuples of values, not as text.** Column *names* are
  ignored: `SELECT name` and `SELECT s.name AS singer` are the same answer.
  Column *order* is not ignored, because selecting the right two columns in the
  wrong order is a real error class.
* **Order matters only if the gold query asks for it.** "List the singers"
  should not fail because the rows came back in a different order, but "list
  them by age" should. Detection looks for ORDER BY at the top level of the
  gold query -- see `has_top_level_order_by`.
* **Otherwise rows are compared as a multiset,** not a set: a missing DISTINCT
  is a real difference and should not be silently forgiven.
* **Numbers are rounded** before comparison, so an average that differs in the
  fifteenth decimal place is not scored as a wrong answer.
* **A prediction that fails to execute is simply wrong.** No partial credit.
"""

from __future__ import annotations

import itertools
import math
from collections import Counter
from dataclasses import dataclass

from .execute import DEFAULT_TIMEOUT_S, ExecResult, execute_sql, strip_sql_comments

# Enough precision that genuinely different numbers stay different, loose enough
# that float accumulation order in AVG/SUM does not decide a benchmark.
FLOAT_PRECISION = 4

# Permutation checking is exponential in the column count, so it is capped.
# Nothing in Spider's dev set selects more than a handful of columns, and a
# 7-column answer that is right apart from its order is not the interesting case.
MAX_PERMUTATION_COLUMNS = 6


@dataclass
class Judgement:
    """Why a prediction was scored the way it was.

    `reason` is deliberately fine-grained. Categorising failures by hand is a
    lot less work when the harness has already separated "wrote invalid SQL"
    from "returned the right rows in the wrong order".
    """

    correct: bool
    reason: str
    gold_rows: int = 0
    pred_rows: int = 0
    # True when the prediction holds exactly the right data and only the column
    # order is wrong. Recorded per failure because it is the one error class
    # that is cheap to count exactly, which makes it the honest way to check
    # whether an instruction aimed at column order actually moved it -- rather
    # than projecting from a hand-labelled sample.
    column_permutation: bool = False

    def __bool__(self) -> bool:
        return self.correct


def has_top_level_order_by(sql: str) -> bool:
    """True if the *outermost* query has an ORDER BY.

    A naive `"order by" in sql.lower()` is wrong twice over: it fires on the
    text inside a string literal, and it fires on an ORDER BY that lives in a
    subquery, which says nothing about the order of the final result. So we
    blank out literals and only look at parenthesis depth zero.

    When in doubt this returns False, which selects the *more forgiving*
    multiset comparison -- a metric should not fail a correct answer because
    the harness misparsed the gold query.
    """
    cleaned = strip_sql_comments(sql)

    depth = 0
    quote: str | None = None
    flattened: list[str] = []
    for ch in cleaned:
        if quote:
            if ch == quote:
                quote = None
            flattened.append(" ")  # blank out literal contents
            continue
        if ch in ("'", '"', "`"):
            quote = ch
            flattened.append(" ")
            continue
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth = max(0, depth - 1)
        # keep characters only while at the outermost level
        flattened.append(ch if depth == 0 else " ")

    return "order by" in " ".join("".join(flattened).lower().split())


def normalize_value(value: object, coerce_numeric_strings: bool = False) -> object:
    """Put one cell into a form two result sets can be compared in.

    `coerce_numeric_strings` exists because Spider's schemas declare some
    columns TEXT that hold numbers (five foreign keys in the dev databases join
    a TEXT column to a NUMBER one), and SQLite's dynamic typing means the same
    value can come back as '1' from one query and 1 from another. It is off by
    default -- turning it on can only ever make the score go up, so it stays an
    explicitly-reported choice rather than a silent one.
    """
    if value is None:
        return None
    if isinstance(value, bool):
        return float(value)
    if isinstance(value, (int, float)):
        if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
            return repr(value)  # NaN != NaN would make a row never match itself
        return round(float(value), FLOAT_PRECISION)
    if isinstance(value, bytes):
        return value
    text = str(value).strip()
    if coerce_numeric_strings:
        try:
            return round(float(text), FLOAT_PRECISION)
        except ValueError:
            pass
    return text


def _rows(result: ExecResult, coerce: bool) -> list[tuple]:
    return [tuple(normalize_value(v, coerce) for v in row) for row in result.rows]


def compare_results(
    gold: ExecResult,
    pred: ExecResult,
    order_matters: bool,
    coerce_numeric_strings: bool = False,
) -> Judgement:
    """Score one prediction against one gold result."""
    if not gold.ok:
        # Every dev gold query executes, so this means the harness broke, not
        # the model. Surfaced as its own reason so it can never be miscounted
        # as a model error.
        return Judgement(False, f"gold_failed:{gold.error_kind}")
    if not pred.ok:
        return Judgement(False, f"exec_error:{pred.error_kind}", gold_rows=len(gold.rows))
    if gold.truncated or pred.truncated:
        # Hitting the row cap means we only saw a prefix; calling that a match
        # would be a guess. Counted separately, not folded into the error rate.
        return Judgement(False, "truncated", len(gold.rows), len(pred.rows))

    g = _rows(gold, coerce_numeric_strings)
    p = _rows(pred, coerce_numeric_strings)
    counts = (len(g), len(p))

    if g and p and len(g[0]) != len(p[0]):
        return Judgement(False, "column_count_mismatch", *counts)

    if Counter(g) != Counter(p):
        return Judgement(False, "value_mismatch", *counts)

    # Same multiset of rows. If the question asked for an order, check it.
    if order_matters and g != p:
        return Judgement(False, "order_mismatch", *counts)

    return Judgement(True, "match", *counts)


def is_column_permutation(
    gold: ExecResult,
    pred: ExecResult,
    order_matters: bool,
    coerce_numeric_strings: bool = False,
) -> bool:
    """True if reordering the predicted columns would make it correct.

    "Same data, wrong column order" is a genuinely different failure from
    "wrong data", and the evaluator's own reason code cannot tell them apart --
    both land in `value_mismatch`, because permuting a tuple changes it.
    """
    if not gold.ok or not pred.ok or not gold.rows or not pred.rows:
        return False
    width = len(pred.rows[0])
    if width != len(gold.rows[0]) or width > MAX_PERMUTATION_COLUMNS or width < 2:
        return False

    for perm in itertools.permutations(range(width)):
        if perm == tuple(range(width)):
            continue  # the identity is the comparison that already failed
        shuffled = ExecResult(
            ok=True,
            rows=[tuple(row[i] for i in perm) for row in pred.rows],
            columns=[pred.columns[i] for i in perm] if pred.columns else [],
        )
        if compare_results(gold, shuffled, order_matters, coerce_numeric_strings).correct:
            return True
    return False


def score(
    db_id: str,
    gold_sql: str,
    predicted_sql: str | None,
    timeout_s: float = DEFAULT_TIMEOUT_S,
    coerce_numeric_strings: bool = False,
) -> Judgement:
    """Execute both queries and compare. Never raises."""
    if not predicted_sql or not predicted_sql.strip():
        return Judgement(False, "no_prediction")

    gold_result = execute_sql(db_id, gold_sql, timeout_s=timeout_s)
    pred_result = execute_sql(db_id, predicted_sql, timeout_s=timeout_s)
    order_matters = has_top_level_order_by(gold_sql)
    judgement = compare_results(
        gold_result,
        pred_result,
        order_matters=order_matters,
        coerce_numeric_strings=coerce_numeric_strings,
    )
    if not judgement.correct:
        judgement.column_permutation = is_column_permutation(
            gold_result, pred_result, order_matters, coerce_numeric_strings
        )
    return judgement


@dataclass
class Summary:
    """Aggregate numbers for one configuration."""

    total: int = 0
    correct: int = 0
    reasons: Counter = None  # type: ignore[assignment]
    empty_gold: int = 0
    column_permutations: int = 0

    def __post_init__(self) -> None:
        if self.reasons is None:
            self.reasons = Counter()

    def add(self, judgement: Judgement) -> None:
        self.total += 1
        self.correct += bool(judgement.correct)
        self.reasons[judgement.reason] += 1
        if judgement.correct and judgement.gold_rows == 0:
            self.empty_gold += 1
        if judgement.column_permutation:
            self.column_permutations += 1

    @property
    def accuracy(self) -> float:
        return self.correct / self.total if self.total else 0.0

    def as_dict(self) -> dict:
        return {
            "total": self.total,
            "correct": self.correct,
            "execution_accuracy": round(self.accuracy, 4),
            "correct_on_empty_gold": self.empty_gold,
            "column_permutation_failures": self.column_permutations,
            "reasons": dict(self.reasons.most_common()),
        }

    def report(self) -> str:
        lines = [
            f"execution accuracy : {self.accuracy:.1%}  ({self.correct}/{self.total})",
            # Split out because an empty gold result is the one case where a
            # wrong query can score correct by returning nothing.
            f"  of which gold was empty: {self.empty_gold}",
            # Split out of the breakdown because it cuts across the reason
            # codes: a permuted answer is always scored `value_mismatch`.
            f"failures that are only a column permutation: {self.column_permutations}",
            "breakdown:",
        ]
        lines += [f"  {reason:26s} {n:5d}" for reason, n in self.reasons.most_common()]
        return "\n".join(lines)
