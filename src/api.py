"""HTTP service: a question in, SQL and rows out.

Serves the run-5 configuration from the ablation -- column instructions, sample
values, three retrieved examples, and execute-and-repair -- with one change:
the repair cap is 1, not 2. The ablation measured both, and they produce the
identical 817 correct answers on the dev set; the only thing the second repair
buys is a longer latency tail. `TEXT2SQL_MAX_REPAIRS` overrides it.

Everything the caller sends is treated as untrusted:

* `db_id` is checked against the databases that exist, not turned straight into
  a path -- `../../somewhere` would otherwise walk out of the data directory.
* `question` is length-capped: it is pasted into a prompt that is paid for by
  the token.
* The SQL that comes back is model output, so it only ever runs through the
  sandbox: read-only connection, single SELECT, timeout, row cap. A question
  that talks the model into writing `DROP TABLE` gets a rejected query back,
  not a dropped table -- and a test says so.

Run:
    python scripts/serve.py                 # http://127.0.0.1:8000/docs
"""

from __future__ import annotations

import os
import time
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from . import config, schema
from .generate import PromptConfig, render_schema
from .llm import DEFAULT_MODEL, LLMClient
from .repair import RepairPolicy, generate_with_repair

SERVE_CONFIG = PromptConfig(
    only_requested_columns=True,
    column_order=True,
    sample_rows=True,
    column_values=True,
    few_shot=3,
)
SERVE_POLICY = RepairPolicy(max_repairs=int(os.environ.get("TEXT2SQL_MAX_REPAIRS", "1")))

# A browser or a client script does not want 100k rows back. The sandbox caps
# what is fetched; this caps what is sent.
MAX_ROWS_RETURNED = 100
MAX_QUESTION_CHARS = 500


class QueryRequest(BaseModel):
    db_id: str = Field(..., min_length=1, max_length=64, examples=["concert_singer"])
    question: str = Field(
        ...,
        min_length=1,
        max_length=MAX_QUESTION_CHARS,
        examples=["How many singers do we have?"],
    )


class Timings(BaseModel):
    """Where the time went, so a slow request can be attributed.

    `llm_ms` is wall time spent waiting on the model (including repairs),
    `sql_ms` is time spent executing queries, and `other_ms` is everything else
    -- retrieval, prompt building, serialisation.
    """

    total_ms: float
    llm_ms: float
    sql_ms: float
    other_ms: float


class QueryResponse(BaseModel):
    db_id: str
    question: str
    sql: str | None
    columns: list[str]
    rows: list[list[Any]]
    row_count: int
    truncated: bool
    repaired: bool
    llm_calls: int
    # Set when no working query was produced. The request itself succeeded --
    # the service did its job and the model did not -- so this is a 200 with
    # an explanation, not a 5xx.
    error: str | None
    timings_ms: Timings


class _TimedClient:
    """Wraps the shared client for one request and measures real wall time.

    The client reports the provider's *recorded* latency on a cache hit, which
    is right for the ablation (it makes latency reproducible) and wrong here:
    a service wants to know how long this request actually waited.
    """

    def __init__(self, inner: LLMClient) -> None:
        self.inner = inner
        self.elapsed_s = 0.0

    def complete(self, *args: Any, **kwargs: Any):
        started = time.perf_counter()
        try:
            return self.inner.complete(*args, **kwargs)
        finally:
            self.elapsed_s += time.perf_counter() - started


def _jsonable(value: Any) -> Any:
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    if isinstance(value, float) and value != value:  # NaN is not valid JSON
        return None
    return value


def known_databases() -> set[str]:
    return {db for db in schema.list_db_ids() if config.db_path(db).exists()}


def create_app(client: LLMClient | None = None, warm: bool = True) -> FastAPI:
    """Build the app. Tests pass a scripted client and skip the warm-up."""
    databases = known_databases()
    llm = client if client is not None else LLMClient()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if warm:
            # About 2.6 s for all 166 databases, measured. Paid once at
            # startup so that no request pays for a cold schema render or the
            # first build of the few-shot index.
            from .retrieve import default_index

            default_index()
            for db_id in databases:
                render_schema(db_id, SERVE_CONFIG)
        yield

    app = FastAPI(
        title="text2sql-agent",
        description="Natural-language questions to SQLite, with execute-and-repair.",
        lifespan=lifespan,
    )

    @app.get("/health")
    def health() -> dict:
        return {
            "status": "ok",
            "model": getattr(llm, "model", DEFAULT_MODEL),
            "config": SERVE_CONFIG.tag,
            "max_repairs": SERVE_POLICY.max_repairs,
            "databases": len(databases),
        }

    @app.get("/databases")
    def list_databases() -> list[str]:
        return sorted(databases)

    # A plain `def`, not `async def`: the model call and SQLite are both
    # blocking, and FastAPI runs sync endpoints on a thread pool. Declaring it
    # async would put those blocking calls on the event loop and serialise
    # every request behind the slowest one.
    @app.post("/query", response_model=QueryResponse)
    def query(req: QueryRequest) -> QueryResponse:
        if req.db_id not in databases:
            raise HTTPException(status_code=404, detail=f"unknown db_id: {req.db_id!r}")

        started = time.perf_counter()
        timed = _TimedClient(llm)
        try:
            trace = generate_with_repair(timed, req.db_id, req.question, SERVE_CONFIG, SERVE_POLICY)
        except Exception as exc:
            # The provider being down is the one failure the caller cannot fix.
            raise HTTPException(status_code=502, detail=f"model call failed: {exc}") from exc

        final = trace.attempts[-1]
        result = final.result
        rows = result.rows if (result and result.ok) else []
        total_s = time.perf_counter() - started
        sql_s = sum(a.exec_s for a in trace.attempts)

        return QueryResponse(
            db_id=req.db_id,
            question=req.question,
            sql=final.sql,
            columns=result.columns if (result and result.ok) else [],
            rows=[[_jsonable(v) for v in row] for row in rows[:MAX_ROWS_RETURNED]],
            row_count=len(rows),
            truncated=len(rows) > MAX_ROWS_RETURNED or bool(result and result.truncated),
            repaired=trace.triggered,
            llm_calls=trace.llm_calls,
            error=None if final.outcome in ("ok", "empty") else final.error,
            timings_ms=Timings(
                total_ms=round(total_s * 1000, 1),
                llm_ms=round(timed.elapsed_s * 1000, 1),
                sql_ms=round(sql_s * 1000, 1),
                other_ms=round(max(0.0, total_s - timed.elapsed_s - sql_s) * 1000, 1),
            ),
        )

    return app


# `uvicorn src.api:app` imports this. Built lazily-safe: creating the client
# does not call the API, so importing the module costs nothing.
app = create_app()
