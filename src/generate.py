"""Turning a question into SQL: prompt construction and response parsing.

Both halves are deliberately visible here rather than hidden behind a library.
The prompt is a plain f-string, so what the model receives is exactly what this
file says it receives, and the cached request on disk can be diffed against it.

The baseline is kept plainly simple -- schema, question, nothing else -- because
it is the number every later improvement is measured against. A baseline that
already has half the tricks in it makes the ablation look worse than it is.
"""

from __future__ import annotations

import functools
import re
from dataclasses import dataclass, field

from . import schema
from .llm import DEFAULT_MAX_TOKENS, LLMClient

BASE_SYSTEM_PROMPT = (
    "You translate natural-language questions into SQLite queries.\n"
    "Reply with a single SQLite SELECT statement and nothing else: "
    "no explanation, no markdown fences, no trailing commentary."
)

# Each line below is switched on by one flag of PromptConfig, so the ablation
# can attribute a change in accuracy to a specific sentence rather than to "the
# prompt got better". The wording is short on purpose: a long instruction block
# competes for attention with the schema.
ONLY_REQUESTED_COLUMNS = (
    "Select only the columns the question asks for. Do not add an id, a count, "
    "or any other column that was not requested."
)
COLUMN_ORDER = (
    "List the selected columns in the order the question mentions them."
)


@dataclass(frozen=True)
class PromptConfig:
    """Which parts of the prompt are switched on.

    Frozen and hashable so a configuration can be passed around, put in a
    filename, and compared. Every field defaults to off, so `PromptConfig()` is
    exactly the baseline that everything else is measured against.
    """

    only_requested_columns: bool = False
    column_order: bool = False
    sample_rows: bool = False
    column_values: bool = False
    few_shot: int = 0  # number of retrieved question/SQL pairs; 0 is off

    @property
    def tag(self) -> str:
        """Short name used for result filenames and the ablation table."""
        parts = [
            name
            for name, on in (
                ("onlycols", self.only_requested_columns),
                ("colorder", self.column_order),
                ("rows", self.sample_rows),
                ("values", self.column_values),
                (f"fs{self.few_shot}", self.few_shot > 0),
            )
            if on
        ]
        return "+".join(["base", *parts])


def build_system_prompt(config: PromptConfig = PromptConfig()) -> str:
    lines = [BASE_SYSTEM_PROMPT]
    if config.only_requested_columns:
        lines.append(ONLY_REQUESTED_COLUMNS)
    if config.column_order:
        lines.append(COLUMN_ORDER)
    return "\n".join(lines)


# Kept so existing callers and the cached baseline responses still resolve to
# the same string.
SYSTEM_PROMPT = BASE_SYSTEM_PROMPT


@dataclass
class Generation:
    """One attempt at answering a question."""

    sql: str | None
    raw_response: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cached: bool = False
    latency_s: float = 0.0
    # Populated by the repair loop; empty for a single-shot generation.
    attempts: list[str] = field(default_factory=list)


_TABLE_NAME = re.compile(r"""CREATE\s+TABLE\s+["\[`]?([A-Za-z_][A-Za-z_0-9]*)""", re.I)


def _table_name(ddl: str) -> str | None:
    match = _TABLE_NAME.search(ddl)
    return match.group(1) if match else None


def _render_sample_rows(db_id: str, table: str) -> str:
    """A few real rows, as SQL comments under the CREATE statement."""
    columns, rows = schema.sample_rows(db_id, table)
    if not rows:
        return ""
    lines = [f"-- {len(rows)} example row(s):", "--   " + " | ".join(columns)]
    lines += ["--   " + " | ".join(schema.format_value(v) for v in row) for row in rows]
    return "\n".join(lines)


def _render_column_values(db_id: str, table: str) -> str:
    """List the values of every text column with few enough to enumerate.

    Aimed at the largest fixable error class in the baseline: a literal in a
    WHERE clause that does not match what the column actually holds. Writing
    `Country = 'France'` is only a mistake if you cannot see that the column
    holds country ids, and `Citizenship != 'French'` is only a mistake if you
    cannot see that it holds 'France'.
    """
    db_schema = schema.load_schema(db_id)
    lines = []
    for column in db_schema.columns_of(table):
        if column.type != "text":
            continue
        n, values = schema.enumerated_values(db_id, table, column.name)
        if not values:
            continue
        shown = ", ".join(schema.format_value(v) for v in values)
        more = "" if n <= len(values) else f", ... ({n} total)"
        lines.append(f"-- {column.name} holds: {shown}{more}")
    return "\n".join(lines)


@functools.lru_cache(maxsize=256)
def render_schema(db_id: str, config: PromptConfig = PromptConfig()) -> str:
    """The schema as the model sees it: the database's own CREATE statements.

    Not a hand-rolled summary. A real DDL is a format the model has seen a very
    large amount of, it carries types, primary keys and foreign keys in one
    place, and it cannot drift out of sync with the database the query will run
    against, because it is read from that database.

    Sample rows and column values are appended as SQL comments, so the block
    stays one valid-looking DDL rather than becoming a second format the model
    has to parse.

    Cached: 20 dev databases answer 1034 questions, so without this the same
    schema -- and its SQLite queries -- is rebuilt fifty times over.
    """
    blocks = []
    for ddl in schema.create_statements(db_id):
        table = _table_name(ddl)
        parts = [ddl]
        if table and config.sample_rows:
            parts.append(_render_sample_rows(db_id, table))
        if table and config.column_values:
            parts.append(_render_column_values(db_id, table))
        blocks.append("\n".join(p for p in parts if p))
    return "\n\n".join(blocks)


def render_few_shot(question: str, k: int) -> str:
    """Retrieved question/SQL pairs, as SQL comments.

    Labelled as coming from other databases because they do: Spider's train and
    dev splits share no schema, so a table name here is not one the model can
    reuse. Saying so is the difference between an example and a trap.

    They go before the schema so that the schema and the question stay adjacent
    -- the examples are context for *how* to answer, the schema is what to
    answer against.
    """
    if k <= 0:
        return ""
    from .retrieve import default_index  # imported lazily: sklearn is slow to load

    examples = default_index().search(question, k=k)
    if not examples:
        return ""
    lines = [
        f"-- {len(examples)} example question/query pairs from other databases,",
        "-- shown for the style of the mapping, not for their table names:",
    ]
    for example in examples:
        lines.append(f"--   Q: {example.question}")
        lines.append(f"--   A: {' '.join(example.gold_sql.split())}")
    return "\n".join(lines)


def build_prompt(db_id: str, question: str, config: PromptConfig = PromptConfig()) -> str:
    """The user message: retrieved examples, schema, then question."""
    blocks = [
        render_few_shot(question, config.few_shot),
        render_schema(db_id, config),
        (
            "-- Using the schema above, write a SQLite query for this question.\n"
            f"-- Question: {question}\n"
            "-- Query:"
        ),
    ]
    return "\n\n".join(b for b in blocks if b)


_FENCE = re.compile(r"```(?:sql|sqlite)?\s*(.*?)```", re.DOTALL | re.IGNORECASE)
_STATEMENT_START = re.compile(r"\b(?:SELECT|WITH)\b", re.IGNORECASE)


def extract_sql(text: str) -> str | None:
    """Pull one SQL statement out of whatever the model replied with.

    Asking for bare SQL is not the same as getting it. In practice a response
    is one of: bare SQL; SQL in a ```sql fence; or SQL with a sentence of
    explanation wrapped around it. All three have to work, because otherwise a
    formatting habit gets scored as a reasoning failure and the error analysis
    points at the wrong thing.
    """
    if not text:
        return None

    fenced = _FENCE.search(text)
    body = fenced.group(1) if fenced else text

    # Drop leading prose: start at the first SELECT/WITH keyword.
    match = _STATEMENT_START.search(body)
    if match is None:
        return None
    body = body[match.start() :]

    # Stop at the first statement terminator, so a trailing "This returns..."
    # or a second example query does not end up in the SQL.
    semicolon = body.find(";")
    if semicolon != -1:
        body = body[:semicolon]

    cleaned = _strip_trailing_prose(body).strip()
    return cleaned or None


def _strip_trailing_prose(body: str) -> str:
    """Cut the response at the first line that cannot be part of the query.

    Used when the model ignored the "nothing else" instruction and there is no
    semicolon to stop at. A blank line, or a line that reads like a sentence
    rather than SQL, ends the statement.
    """
    lines: list[str] = []
    for line in body.splitlines():
        stripped = line.strip()
        if not stripped:
            if lines:  # blank line after the query has started
                break
            continue
        # A line of prose: starts with a capitalised word and has no SQL
        # punctuation or keyword anywhere in it.
        if lines and _looks_like_prose(stripped):
            break
        lines.append(line)
    return "\n".join(lines)


_SQL_TOKENS = re.compile(
    r"\b(SELECT|FROM|WHERE|JOIN|GROUP|ORDER|HAVING|LIMIT|UNION|AND|OR|ON|AS|"
    r"COUNT|SUM|AVG|MIN|MAX|DISTINCT|INNER|LEFT|OUTER|NOT|IN|LIKE|BETWEEN|"
    r"EXCEPT|INTERSECT|CASE|WHEN|THEN|ELSE|END|DESC|ASC|BY)\b",
    re.IGNORECASE,
)


def _looks_like_prose(line: str) -> bool:
    if _SQL_TOKENS.search(line):
        return False
    return not any(ch in line for ch in "(),*=<>")


def generate_sql(
    client: LLMClient,
    db_id: str,
    question: str,
    config: PromptConfig = PromptConfig(),
    temperature: float = 0.0,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    cache_salt: str = "",
) -> Generation:
    """One question in, one candidate query out."""
    prompt = build_prompt(db_id, question, config)
    response = client.complete(
        user=prompt,
        system=build_system_prompt(config),
        temperature=temperature,
        max_tokens=max_tokens,
        cache_salt=cache_salt,
    )
    return Generation(
        sql=extract_sql(response.text),
        raw_response=response.text,
        prompt_tokens=response.input_tokens,
        completion_tokens=response.output_tokens,
        cached=response.cached,
        latency_s=response.latency_s,
    )
