"""Tests for the execution-accuracy rules.

Every one of these pins down a decision that changes the headline number, so
they are written as the *claims* the metric makes rather than as coverage.
Runs without the dataset or an API key.
"""

from __future__ import annotations

import sqlite3
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.evaluate import (  # noqa: E402
    compare_results,
    has_top_level_order_by,
    normalize_value,
    score,
)
from src.execute import ExecResult  # noqa: E402


def _result(rows: list[tuple], columns: list[str] | None = None) -> ExecResult:
    return ExecResult(ok=True, rows=rows, columns=columns or ["c"] * (len(rows[0]) if rows else 1))


def _fixture_db() -> Path:
    path = Path(tempfile.mkdtemp()) / "fixture.sqlite"
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE city (name TEXT, country TEXT, pop INTEGER)")
    conn.executemany(
        "INSERT INTO city VALUES (?, ?, ?)",
        [("Boston", "USA", 654), ("Lyon", "France", 513), ("Kyoto", "Japan", 1463)],
    )
    conn.commit()
    conn.close()
    return path


# --------------------------------------------------------------- ORDER BY

def test_order_by_detected_only_at_top_level() -> None:
    assert has_top_level_order_by("SELECT a FROM t ORDER BY a")
    assert has_top_level_order_by("select a from t order by a desc limit 3")
    # inside a subquery: says nothing about the order of the final result
    assert not has_top_level_order_by(
        "SELECT name FROM (SELECT name FROM t ORDER BY x LIMIT 3)"
    )
    assert not has_top_level_order_by("SELECT a FROM t WHERE a IN (SELECT b FROM u ORDER BY b)")
    # inside a string literal
    assert not has_top_level_order_by("SELECT a FROM t WHERE note = 'order by date'")
    # behind a comment
    assert not has_top_level_order_by("SELECT a FROM t -- order by a")


def test_order_ignored_when_gold_is_unordered() -> None:
    gold = _result([("a",), ("b",)])
    pred = _result([("b",), ("a",)])
    assert compare_results(gold, pred, order_matters=False).correct
    j = compare_results(gold, pred, order_matters=True)
    assert not j.correct and j.reason == "order_mismatch"


# ----------------------------------------------------------- row semantics

def test_duplicates_are_not_collapsed() -> None:
    """A missing DISTINCT is a real error, so rows are a multiset, not a set."""
    gold = _result([("a",), ("a",), ("b",)])
    pred = _result([("a",), ("b",)])
    j = compare_results(gold, pred, order_matters=False)
    assert not j.correct and j.reason == "value_mismatch"


def test_column_order_matters() -> None:
    gold = _result([("Boston", 654)])
    pred = _result([(654, "Boston")])
    assert not compare_results(gold, pred, order_matters=False).correct


def test_column_count_mismatch_is_reported_separately() -> None:
    gold = _result([("Boston",)])
    pred = _result([("Boston", 654)])
    j = compare_results(gold, pred, order_matters=False)
    assert not j.correct and j.reason == "column_count_mismatch"


def test_failed_prediction_is_wrong_not_an_exception() -> None:
    gold = _result([("a",)])
    pred = ExecResult(ok=False, error="no such column: x", error_kind="sql_error")
    j = compare_results(gold, pred, order_matters=False)
    assert not j.correct and j.reason == "exec_error:sql_error"


def test_truncated_results_are_not_guessed_at() -> None:
    gold = ExecResult(ok=True, rows=[("a",)], truncated=True)
    pred = ExecResult(ok=True, rows=[("a",)])
    assert compare_results(gold, pred, order_matters=False).reason == "truncated"


# ------------------------------------------------------------ value rules

def test_float_noise_does_not_decide_the_benchmark() -> None:
    gold = _result([(10621.666666666666,)])
    pred = _result([(10621.666666666667,)])
    assert compare_results(gold, pred, order_matters=False).correct


def test_int_and_float_spellings_of_the_same_number_match() -> None:
    assert normalize_value(1) == normalize_value(1.0)
    assert compare_results(_result([(1,)]), _result([(1.0,)]), order_matters=False).correct


def test_numeric_strings_are_distinct_unless_coercion_is_asked_for() -> None:
    """SQLite's dynamic typing can return '1' where gold returned 1.

    Off by default: coercion can only raise the score, so it stays an explicit,
    reported choice rather than a silent one.
    """
    gold, pred = _result([(1,)]), _result([("1",)])
    assert not compare_results(gold, pred, order_matters=False).correct
    assert compare_results(gold, pred, order_matters=False, coerce_numeric_strings=True).correct


def test_nulls_compare_equal_to_nulls() -> None:
    assert compare_results(_result([(None,)]), _result([(None,)]), order_matters=False).correct


# --------------------------------------------------------- end to end

def test_equivalent_sql_spellings_score_correct() -> None:
    """The whole reason for execution accuracy over string matching."""
    db = _fixture_db()
    gold = "SELECT name FROM city WHERE pop > 600"
    for equivalent in [
        "SELECT c.name AS city_name FROM city AS c WHERE c.pop > 600",
        "select NAME from CITY where POP >= 601",
        "SELECT name FROM city WHERE pop BETWEEN 601 AND 99999",
    ]:
        assert score(db, gold, equivalent).correct, equivalent


def test_wrong_sql_scores_wrong() -> None:
    db = _fixture_db()
    gold = "SELECT name FROM city WHERE pop > 600"
    assert not score(db, gold, "SELECT name FROM city").correct
    assert not score(db, gold, "SELECT name FROM city WHERE pop > 600 LIMIT 1").correct
    assert not score(db, gold, "SELECT nope FROM city").correct
    assert not score(db, gold, None).correct
    assert not score(db, gold, "   ").correct


def test_generated_write_statement_is_wrong_not_executed() -> None:
    db = _fixture_db()
    j = score(db, "SELECT name FROM city", "DROP TABLE city")
    assert not j.correct and j.reason == "exec_error:rejected"


if __name__ == "__main__":
    passed = failed = 0
    for name, fn in sorted(globals().items()):
        if not name.startswith("test_") or not callable(fn):
            continue
        try:
            fn()
            print(f"PASS  {name}")
            passed += 1
        except AssertionError as exc:
            print(f"FAIL  {name}: {exc}")
            failed += 1
        except Exception as exc:  # noqa: BLE001
            print(f"ERROR {name}: {type(exc).__name__}: {exc}")
            failed += 1
    print(f"\n{passed} passed, {failed} failed")
    raise SystemExit(1 if failed else 0)
