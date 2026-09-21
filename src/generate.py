"""Turning a question into SQL: prompt construction and response parsing.

Both halves are deliberately visible here rather than hidden behind a library.
The prompt is a plain f-string, so what the model receives is exactly what this
file says it receives, and the cached request on disk can be diffed against it.

The baseline is kept plainly simple -- schema, question, nothing else -- because
it is the number every later improvement is measured against. A baseline that
already has half the tricks in it makes the ablation look worse than it is.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from . import schema
from .llm import DEFAULT_MAX_TOKENS, LLMClient

SYSTEM_PROMPT = (
    "You translate natural-language questions into SQLite queries.\n"
    "Reply with a single SQLite SELECT statement and nothing else: "
    "no explanation, no markdown fences, no trailing commentary."
)


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


def render_schema(db_id: str) -> str:
    """The schema as the model sees it: the database's own CREATE statements.

    Not a hand-rolled summary. A real DDL is a format the model has seen a very
    large amount of, it carries types, primary keys and foreign keys in one
    place, and it cannot drift out of sync with the database the query will run
    against, because it is read from that database.
    """
    return "\n\n".join(schema.create_statements(db_id))


def build_prompt(db_id: str, question: str) -> str:
    """The baseline user message: schema, then question."""
    return (
        f"{render_schema(db_id)}\n\n"
        f"-- Using the schema above, write a SQLite query for this question.\n"
        f"-- Question: {question}\n"
        f"-- Query:"
    )


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
    temperature: float = 0.0,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    cache_salt: str = "",
) -> Generation:
    """One question in, one candidate query out."""
    prompt = build_prompt(db_id, question)
    response = client.complete(
        user=prompt,
        system=SYSTEM_PROMPT,
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
