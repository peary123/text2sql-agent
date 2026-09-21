"""Tests for the execution sandbox.

Run with:  python -m pytest tests/ -q      (or: python tests/test_execute.py)

These are the tests worth having early: they pin down the two properties the
whole evaluation rests on -- generated SQL cannot write, and generated SQL
cannot hang -- and they run without the dataset or an API key.
"""

from __future__ import annotations

import sqlite3
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.execute import execute_sql, is_read_only, strip_sql_comments  # noqa: E402


def _fixture_db() -> Path:
    """A throwaway database with one small table."""
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


def test_comment_stripping_preserves_literals() -> None:
    assert strip_sql_comments("SELECT 1 -- trailing") .strip() == "SELECT 1"
    assert strip_sql_comments("SELECT /* mid */ 1").split() == ["SELECT", "1"]
    # a literal that merely looks like a comment must survive
    assert "--x--" in strip_sql_comments("SELECT '--x--' FROM t")


def test_read_only_guard() -> None:
    allowed = [
        "SELECT 1",
        "select * from city;",
        "WITH a AS (SELECT 1 AS x) SELECT x FROM a",
        "SELECT '--' FROM city",
    ]
    refused = [
        "DROP TABLE city",
        "DELETE FROM city",
        "UPDATE city SET pop = 0",
        "INSERT INTO city VALUES ('x','y',1)",
        "SELECT 1; DROP TABLE city",  # statement smuggled after a semicolon
        "-- harmless\nDROP TABLE city",  # hidden behind a comment
        "/* c */ DELETE FROM city",
        "ATTACH DATABASE 'other.db' AS o",  # could open a writable handle
        "PRAGMA writable_schema = 1",
        "",
    ]
    for sql in allowed:
        assert is_read_only(sql), sql
    for sql in refused:
        assert not is_read_only(sql), sql


def test_writes_are_refused_and_db_is_untouched() -> None:
    db = _fixture_db()
    result = execute_sql(db, "DROP TABLE city")
    assert not result.ok and result.error_kind == "rejected"

    # Belt and braces: even with the guard disabled the connection is read-only.
    result = execute_sql(db, "DROP TABLE city", enforce_read_only=False)
    assert not result.ok and result.error_kind == "sql_error"

    survivors = execute_sql(db, "SELECT count(*) FROM city")
    assert survivors.ok and survivors.rows == [(3,)]


def test_successful_query_returns_rows_and_columns() -> None:
    db = _fixture_db()
    result = execute_sql(db, "SELECT name, pop FROM city WHERE country = 'USA'")
    assert result.ok
    assert result.columns == ["name", "pop"]
    assert result.rows == [("Boston", 654)]


def test_sql_error_is_returned_not_raised() -> None:
    db = _fixture_db()
    result = execute_sql(db, "SELECT nonexistent FROM city")
    assert not result.ok and result.error_kind == "sql_error"
    assert "nonexistent" in (result.error or "")


def test_runaway_query_times_out() -> None:
    """A self-join with no join condition is the classic model failure mode."""
    db = _fixture_db()
    cartesian = (
        "WITH RECURSIVE big(n) AS (SELECT 1 UNION ALL SELECT n + 1 FROM big) "
        "SELECT count(*) FROM big"
    )
    result = execute_sql(db, cartesian, timeout_s=1.0)
    assert not result.ok and result.error_kind == "timeout"
    assert result.elapsed_s < 4.0  # the deadline actually bit


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
