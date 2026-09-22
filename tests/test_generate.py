"""Tests for pulling SQL out of a model response.

These matter more than they look. If extraction is sloppy, a model that wrote
perfectly good SQL inside a markdown fence is scored as wrong, the error
analysis reports a reasoning problem, and the next three days are spent fixing
the wrong thing.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.generate import (  # noqa: E402
    BASE_SYSTEM_PROMPT,
    PromptConfig,
    build_system_prompt,
    extract_sql,
)


def test_bare_sql() -> None:
    assert extract_sql("SELECT name FROM city") == "SELECT name FROM city"


def test_trailing_semicolon_is_dropped() -> None:
    assert extract_sql("SELECT name FROM city;") == "SELECT name FROM city"


def test_sql_fence() -> None:
    assert extract_sql("```sql\nSELECT name FROM city\n```") == "SELECT name FROM city"
    assert extract_sql("```\nSELECT name FROM city\n```") == "SELECT name FROM city"
    assert extract_sql("```sqlite\nSELECT 1\n```") == "SELECT 1"


def test_leading_prose_is_dropped() -> None:
    text = "Here is the query you asked for:\n\nSELECT name FROM city"
    assert extract_sql(text) == "SELECT name FROM city"


def test_trailing_prose_is_dropped() -> None:
    text = "SELECT name FROM city\n\nThis returns every city name."
    assert extract_sql(text) == "SELECT name FROM city"


def test_prose_on_the_next_line_without_a_blank_line() -> None:
    text = "SELECT name FROM city\nThis lists all of them."
    assert extract_sql(text) == "SELECT name FROM city"


def test_multiline_sql_survives() -> None:
    text = "```sql\nSELECT c.name\nFROM city AS c\nWHERE c.pop > 100\nORDER BY c.pop DESC\n```"
    out = extract_sql(text)
    assert out is not None
    assert out.splitlines()[0] == "SELECT c.name"
    assert out.splitlines()[-1] == "ORDER BY c.pop DESC"


def test_with_clause_is_a_valid_start() -> None:
    text = "WITH top AS (SELECT * FROM city LIMIT 5) SELECT name FROM top"
    assert extract_sql(text) == text


def test_only_the_first_statement_is_taken() -> None:
    """A second example query must not be appended to the first."""
    text = "SELECT name FROM city; SELECT * FROM country"
    assert extract_sql(text) == "SELECT name FROM city"


def test_no_sql_at_all() -> None:
    assert extract_sql("I cannot answer that.") is None
    assert extract_sql("") is None
    assert extract_sql("```\n\n```") is None


def test_sql_keyword_inside_prose_is_not_mistaken_for_a_query() -> None:
    """A line of explanation containing no SQL punctuation ends the query."""
    text = "SELECT a FROM t\nNote that ordering is unspecified here"
    assert extract_sql(text) == "SELECT a FROM t"


# ------------------------------------------------------ prompt configuration

def test_default_config_is_exactly_the_baseline() -> None:
    """Every later number is a delta against this, so it must not drift.

    The cache is keyed on the prompt text, so a stray character here would
    silently invalidate every cached baseline response and re-bill the run.
    """
    assert build_system_prompt() == BASE_SYSTEM_PROMPT
    assert build_system_prompt(PromptConfig()) == BASE_SYSTEM_PROMPT
    assert PromptConfig().tag == "base"


def test_each_flag_adds_exactly_its_own_line() -> None:
    base_lines = len(BASE_SYSTEM_PROMPT.splitlines())
    only = build_system_prompt(PromptConfig(only_requested_columns=True))
    order = build_system_prompt(PromptConfig(column_order=True))
    both = build_system_prompt(PromptConfig(only_requested_columns=True, column_order=True))

    assert len(only.splitlines()) == base_lines + 1
    assert len(order.splitlines()) == base_lines + 1
    assert len(both.splitlines()) == base_lines + 2
    # each single-flag prompt is a prefix of the combined one in flag order
    assert both.startswith(only)


def test_tags_are_distinct_and_stable() -> None:
    """Tags name result files, so two configurations must never collide."""
    tags = {
        PromptConfig().tag,
        PromptConfig(only_requested_columns=True).tag,
        PromptConfig(column_order=True).tag,
        PromptConfig(only_requested_columns=True, column_order=True).tag,
    }
    assert len(tags) == 4
    assert PromptConfig(column_order=True).tag == "base+colorder"


def test_config_is_hashable() -> None:
    """Frozen, so a configuration can key a dict of runs."""
    assert len({PromptConfig(), PromptConfig(), PromptConfig(column_order=True)}) == 2


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
