"""Tests for the schema enrichment that goes into the prompt.

These need the Spider data, and are skipped without it, because the point of
the enrichment is that it reflects what is actually stored -- a test against a
fixture would only check the formatting.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src import config, schema  # noqa: E402
from src.generate import PromptConfig, render_schema  # noqa: E402

HAVE_DATA = config.DEV_JSON.exists()


def test_format_value_keeps_whitespace_visible() -> None:
    """flight_2.airports.Country holds 'United States ' -- with a trailing space.

    A model that cannot see the space writes a predicate matching nothing, so
    values are rendered with repr(), not str().
    """
    assert schema.format_value("United States ") == "'United States '"
    assert schema.format_value(None) == "NULL"
    assert schema.format_value(42) == "42"
    assert len(schema.format_value("x" * 200)) <= schema.MAX_VALUE_LENGTH + 2


def test_high_cardinality_columns_are_not_enumerated() -> None:
    """Five example surnames say nothing about the sixth."""
    if not HAVE_DATA:
        return
    n, values = schema.enumerated_values("wta_1", "players", "last_name")
    assert n > schema.MAX_DISTINCT_TO_ENUMERATE
    assert values == ()


def test_low_cardinality_columns_are_enumerated() -> None:
    if not HAVE_DATA:
        return
    n, values = schema.enumerated_values("car_1", "continents", "Continent")
    assert 0 < n <= schema.MAX_DISTINCT_TO_ENUMERATE
    assert "europe" in values


def test_enrichment_reveals_what_a_column_really_holds() -> None:
    """The specific failures this is meant to fix.

    car_makers.Country holds country ids, not names -- the baseline wrote
    WHERE Country = 'France' against it. Ref_Template_Types.Template_Type_Code
    holds 'PPT' -- the baseline looked 'PPT' up as a description.
    """
    if not HAVE_DATA:
        return
    rendered = render_schema("car_1", PromptConfig(column_values=True))
    assert "Country holds: '1'" in rendered
    assert "'usa'" in rendered  # CountryName is lowercase in the data

    rendered = render_schema(
        "cre_Doc_Template_Mgt", PromptConfig(column_values=True)
    )
    assert "'PPT'" in rendered


def test_baseline_rendering_is_unchanged() -> None:
    """The default config must still produce exactly the baseline prompt."""
    if not HAVE_DATA:
        return
    plain = render_schema("car_1")
    assert "--" not in plain
    assert plain == "\n\n".join(schema.create_statements("car_1"))


def test_each_flag_only_adds_its_own_block() -> None:
    if not HAVE_DATA:
        return
    rows = render_schema("car_1", PromptConfig(sample_rows=True))
    values = render_schema("car_1", PromptConfig(column_values=True))
    assert "example row(s)" in rows and "holds:" not in rows
    assert "holds:" in values and "example row(s)" not in values


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
        except Exception as exc:
            print(f"ERROR {name}: {type(exc).__name__}: {exc}")
            failed += 1
    print(f"\n{passed} passed, {failed} failed")
    raise SystemExit(1 if failed else 0)
