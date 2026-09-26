"""Execute-and-repair: run the generated SQL, and if it fails, show the model why.

This is the one stage that uses information the prompt alone does not have: the
result of actually running the query. By the time it runs, four stages of prompt
work have left 33 of 1034 dev queries failing with an explicit SQLite error,
most of them `no such column` from a dropped join. That error message names the
problem precisely, which makes it about the best feedback a model can get.

Two rules this module is built around:

* **It never sees the gold query.** The only input to a repair decision is the
  predicted query's own execution result -- did it error, did it return rows.
  That is information a deployed system has; gold is not. `generate_with_repair`
  does not take a gold argument at all, so this is structural, not a matter of
  being careful, and a test pins the signature.

* **Every attempt is recorded.** The cap on repairs is a claim ("a second repair
  is still worth its cost, a third is not"), and a claim needs the per-attempt
  numbers behind it.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .execute import DEFAULT_TIMEOUT_S, ExecResult, execute_sql
from .generate import (
    Generation,
    PromptConfig,
    build_prompt,
    build_system_prompt,
    extract_sql,
    generate_sql,
)
from .llm import DEFAULT_MAX_TOKENS, LLMClient

# Failures a rewrite can plausibly fix. `db_error` is excluded: it means the
# database file is missing or unreadable, which no amount of rewording changes.
_REPAIRABLE_KINDS = {"sql_error", "timeout", "rejected"}


@dataclass(frozen=True)
class RepairPolicy:
    """When to ask the model for a second attempt, and how many times.

    Defaults to off, so `RepairPolicy()` leaves generation exactly as it was.
    """

    max_repairs: int = 0
    # Repair a query that ran cleanly but returned nothing. Riskier than
    # repairing an error: 49 dev gold queries legitimately return an empty
    # result, and for those the model is being pushed to change a right answer.
    on_empty: bool = False

    @property
    def tag(self) -> str:
        if self.max_repairs <= 0:
            return ""
        return f"repair{self.max_repairs}" + ("+empty" if self.on_empty else "")


@dataclass
class Attempt:
    """One query the model produced, and what happened when it was run."""

    sql: str | None
    outcome: str  # "ok" | "empty" | "no_sql" | an ExecResult.error_kind
    error: str | None = None
    raw: str = ""  # the model's full response, kept to check how repairs are phrased
    # Kept so a caller can return the rows without running the query twice.
    # Not serialised by the evaluation runner, which only needs the outcome.
    result: ExecResult | None = field(default=None, repr=False)

    @property
    def exec_s(self) -> float:
        return self.result.elapsed_s if self.result else 0.0


@dataclass
class RepairTrace:
    """The full history of one question through the loop."""

    attempts: list[Attempt] = field(default_factory=list)
    llm_calls: int = 0
    llm_latency_s: float = 0.0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cached: bool = True  # true only if every call was served from cache

    @property
    def final_sql(self) -> str | None:
        return self.attempts[-1].sql if self.attempts else None

    @property
    def repairs_used(self) -> int:
        return max(0, len(self.attempts) - 1)

    @property
    def triggered(self) -> bool:
        return self.repairs_used > 0


def diagnose(db_id: str, sql: str | None, policy: RepairPolicy,
             timeout_s: float = DEFAULT_TIMEOUT_S) -> Attempt:
    """Run a candidate query and classify the outcome -- using nothing but it.

    This is the only place the loop looks at a database, and it only ever runs
    the *predicted* query.
    """
    if not sql:
        return Attempt(sql=sql, outcome="no_sql", error="the response contained no SQL query")
    result: ExecResult = execute_sql(db_id, sql, timeout_s=timeout_s)
    if not result.ok:
        return Attempt(sql=sql, outcome=result.error_kind or "sql_error",
                       error=result.error, result=result)
    if not result.rows:
        return Attempt(sql=sql, outcome="empty", result=result)
    return Attempt(sql=sql, outcome="ok", result=result)


def needs_repair(attempt: Attempt, policy: RepairPolicy) -> bool:
    if attempt.outcome in _REPAIRABLE_KINDS or attempt.outcome == "no_sql":
        return True
    return attempt.outcome == "empty" and policy.on_empty


def build_repair_prompt(db_id: str, question: str, config: PromptConfig,
                        failed: Attempt) -> str:
    """The original prompt, followed by the failed query and what went wrong.

    The full original prompt is repeated rather than summarised, so the model
    repairs against exactly the schema, sample values and examples it had the
    first time -- a repair that sees a different schema is a different
    experiment.
    """
    original = build_prompt(db_id, question, config)
    # Drop the trailing "-- Query:" so the follow-up reads as a continuation.
    if original.endswith("-- Query:"):
        original = original[: -len("-- Query:")].rstrip()

    shown_sql = " ".join((failed.sql or "").split()) or "(none)"
    if failed.outcome == "empty":
        feedback = [
            "-- It ran without error but returned no rows.",
            "-- If the question should have an answer, check each literal against",
            "-- the column values listed above, and check every join. If an empty",
            "-- result is genuinely correct, repeat the query unchanged.",
        ]
    elif failed.outcome == "no_sql":
        feedback = ["-- The previous response did not contain a SQL query."]
    else:
        feedback = [f"-- SQLite rejected it: {failed.error}"]

    return "\n".join(
        [
            original,
            "",
            "-- Your previous query:",
            f"--   {shown_sql}",
            *feedback,
            "-- Write a corrected SQLite query for the same question.",
            "-- Query:",
        ]
    )


def generate_with_repair(
    client: LLMClient,
    db_id: str,
    question: str,
    config: PromptConfig,
    policy: RepairPolicy,
    max_tokens: int = DEFAULT_MAX_TOKENS,
) -> RepairTrace:
    """Generate a query, run it, and repair it up to `policy.max_repairs` times.

    Deliberately takes no gold query. What a repair can act on is exactly what a
    deployed system would know: the predicted query's own execution result.
    """
    trace = RepairTrace()

    first: Generation = generate_sql(client, db_id, question, config, max_tokens=max_tokens)
    _account(trace, first.prompt_tokens, first.completion_tokens, first.latency_s, first.cached)
    attempt = diagnose(db_id, first.sql, policy)
    attempt.raw = first.raw_response
    trace.attempts.append(attempt)

    system = build_system_prompt(config)
    for _ in range(policy.max_repairs):
        if not needs_repair(attempt, policy):
            break
        response = client.complete(
            user=build_repair_prompt(db_id, question, config, attempt),
            system=system,
            max_tokens=max_tokens,
        )
        _account(trace, response.input_tokens, response.output_tokens,
                 response.latency_s, response.cached)
        attempt = diagnose(db_id, extract_sql(response.text), policy)
        attempt.raw = response.text
        trace.attempts.append(attempt)

    return trace


def _account(trace: RepairTrace, tokens_in: int, tokens_out: int,
             latency_s: float, cached: bool) -> None:
    trace.llm_calls += 1
    trace.prompt_tokens += tokens_in
    trace.completion_tokens += tokens_out
    trace.llm_latency_s += latency_s
    trace.cached = trace.cached and cached
