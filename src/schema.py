"""Reading database schemas.

There are two sources of schema information in Spider and they are not
interchangeable:

* `tables.json` -- Spider's own annotation: table/column names, types, primary
  keys and foreign keys, in a structured form. Convenient to reason about.
* `sqlite_master` -- the actual CREATE TABLE statements in the database file.
  This is ground truth for what a query can reference, and it is what the model
  will be shown, because a real DDL is a format the model has seen a lot of.

We read both: the DDL goes in the prompt, the structured form is what sample
values get hung off when the prompt is enriched with real column contents.
"""

from __future__ import annotations

import functools
import json
import sqlite3
from dataclasses import dataclass, field

from . import config
from .execute import _connect


@dataclass
class Column:
    name: str
    table: str
    type: str  # Spider's coarse type: text / number / time / boolean / others
    is_primary_key: bool = False


@dataclass
class ForeignKey:
    from_table: str
    from_column: str
    to_table: str
    to_column: str


@dataclass
class DBSchema:
    db_id: str
    tables: list[str] = field(default_factory=list)
    columns: list[Column] = field(default_factory=list)
    foreign_keys: list[ForeignKey] = field(default_factory=list)

    def columns_of(self, table: str) -> list[Column]:
        return [c for c in self.columns if c.table.lower() == table.lower()]

    def text_columns(self) -> list[Column]:
        """Columns whose values a WHERE clause is likely to compare against.

        Used by the sample-value enrichment: showing the model that a country
        column holds "USA" and not "United States" is what fixes the largest
        single error class in the baseline.
        """
        return [c for c in self.columns if c.type == "text"]


@functools.lru_cache(maxsize=1)
def _tables_json() -> dict[str, dict]:
    """tables.json, indexed by db_id (parsed once per process)."""
    config.require_data()
    with config.TABLES_JSON.open(encoding="utf-8") as fh:
        return {entry["db_id"]: entry for entry in json.load(fh)}


def list_db_ids() -> list[str]:
    return sorted(_tables_json())


@functools.lru_cache(maxsize=256)
def load_schema(db_id: str) -> DBSchema:
    """Structured schema for `db_id`, built from tables.json."""
    entry = _tables_json().get(db_id)
    if entry is None:
        raise KeyError(f"unknown db_id: {db_id}")

    tables: list[str] = entry["table_names_original"]
    primary_keys = set(entry.get("primary_keys", []))

    columns: list[Column] = []
    # column_names_original is [[table_index, column_name], ...]; index 0 is the
    # synthetic "*" column, which belongs to no table.
    for col_index, (table_index, col_name) in enumerate(entry["column_names_original"]):
        if table_index < 0:
            continue
        columns.append(
            Column(
                name=col_name,
                table=tables[table_index],
                type=entry["column_types"][col_index],
                is_primary_key=col_index in primary_keys,
            )
        )

    # foreign_keys is [[from_column_index, to_column_index], ...] into the same
    # flat column list, so we resolve through it rather than by name.
    flat = entry["column_names_original"]
    foreign_keys: list[ForeignKey] = []
    for src, dst in entry.get("foreign_keys", []):
        s_tab, s_col = flat[src]
        d_tab, d_col = flat[dst]
        if s_tab < 0 or d_tab < 0:
            continue
        foreign_keys.append(
            ForeignKey(
                from_table=tables[s_tab],
                from_column=s_col,
                to_table=tables[d_tab],
                to_column=d_col,
            )
        )

    return DBSchema(db_id=db_id, tables=list(tables), columns=columns, foreign_keys=foreign_keys)


@functools.lru_cache(maxsize=256)
def create_statements(db_id: str) -> list[str]:
    """The CREATE TABLE statements stored in the database file itself.

    Skips SQLite's internal tables (sqlite_sequence and friends), whose names
    are reserved and which no user query should reference.
    """
    path = config.db_path(db_id)
    if not path.exists():
        raise FileNotFoundError(f"database not found: {path}")
    conn = _connect(path)
    try:
        rows = conn.execute(
            "SELECT sql FROM sqlite_master "
            "WHERE type = 'table' AND name NOT LIKE 'sqlite_%' AND sql IS NOT NULL "
            "ORDER BY name"
        ).fetchall()
    finally:
        conn.close()
    return [_normalise_ddl(r[0]) for r in rows]


def _normalise_ddl(ddl: str) -> str:
    """Collapse the ragged whitespace Spider's DDL ships with.

    Purely cosmetic, but the DDL goes straight into the prompt and stray tabs
    and blank lines cost tokens on every single request.
    """
    lines = [line.rstrip() for line in ddl.replace("\t", "    ").splitlines()]
    return "\n".join(line for line in lines if line.strip())


def n_columns(db_id: str) -> int:
    return len(load_schema(db_id).columns)


# Enumerating a column's values only teaches the model something when there are
# few enough of them to be a closed set. Measured across the 20 dev databases:
# 74% of text columns have 20 or fewer distinct values -- continents, country
# codes, template types, sexes -- and those are exactly the columns a WHERE
# clause compares a literal against. The remaining columns are names and
# addresses, where five examples say nothing about the sixth.
MAX_DISTINCT_TO_ENUMERATE = 20
MAX_VALUES_SHOWN = 10
MAX_VALUE_LENGTH = 40
SAMPLE_ROWS = 3


@functools.lru_cache(maxsize=512)
def sample_rows(db_id: str, table: str, limit: int = SAMPLE_ROWS) -> tuple[list[str], list[tuple]]:
    """A few real rows from `table`, as (column names, rows)."""
    conn = _connect(config.db_path(db_id))
    try:
        cur = conn.execute(f'SELECT * FROM "{table}" LIMIT {int(limit)}')
        columns = [d[0] for d in cur.description] if cur.description else []
        return columns, [tuple(r) for r in cur.fetchall()]
    except sqlite3.Error:
        # A table we cannot read should not take the whole prompt down with it.
        return [], []
    finally:
        conn.close()


@functools.lru_cache(maxsize=2048)
def enumerated_values(db_id: str, table: str, column: str) -> tuple[int, tuple]:
    """(distinct count, values to show) for a low-cardinality text column.

    Returns an empty tuple of values when the column has too many distinct
    values to be worth listing, so the caller can tell "nothing to show" from
    "genuinely empty".
    """
    conn = _connect(config.db_path(db_id))
    try:
        n = conn.execute(
            f'SELECT COUNT(DISTINCT "{column}") FROM "{table}"'
        ).fetchone()[0]
        if not n or n > MAX_DISTINCT_TO_ENUMERATE:
            return int(n or 0), ()
        rows = conn.execute(
            f'SELECT DISTINCT "{column}" FROM "{table}" '
            f'WHERE "{column}" IS NOT NULL LIMIT {MAX_VALUES_SHOWN}'
        ).fetchall()
        return int(n), tuple(r[0] for r in rows)
    except sqlite3.Error:
        return 0, ()
    finally:
        conn.close()


def format_value(value: object) -> str:
    """Render one cell for the prompt.

    `repr` rather than `str`, deliberately: it keeps the quotes and makes
    whitespace visible. `flight_2.airports.Country` holds `'United States '`
    with a trailing space, and a model that cannot see that will write a
    predicate that matches nothing.
    """
    if value is None:
        return "NULL"
    if isinstance(value, (int, float)):
        return str(value)
    text = str(value)
    if len(text) > MAX_VALUE_LENGTH:
        text = text[: MAX_VALUE_LENGTH - 1] + "…"
    return repr(text)
