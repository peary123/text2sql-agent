"""Sandboxed execution of SQL against a Spider SQLite database.

The SQL we run here is written by a language model, so two things have to be
true no matter what it produces:

  1. it cannot modify anything  -> the connection is opened read-only
  2. it cannot hang the harness -> every query has a wall-clock deadline

There is a third, less obvious requirement: several Spider databases contain
byte sequences that are not valid UTF-8, and the default sqlite3 text factory
raises on them mid-fetch. We decode leniently instead so that one dirty row in
a database does not look like a model failure.
"""

from __future__ import annotations

import queue
import sqlite3
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from . import config

DEFAULT_TIMEOUT_S = 10.0

# The cap exists for the query that is *fast* and returns ten million rows --
# the timeout does not catch that one, and it exhausts memory just as well as a
# slow query does. The number is measured, not guessed: the largest legitimate
# result in the Spider dev set is 20,662 rows (all of wta_1.players), and the
# next largest is 2,217. 100k leaves ~5x headroom over anything the benchmark
# legitimately asks for while still bounding memory to tens of megabytes.
MAX_ROWS = 100_000

# Statements we are willing to run. Anything else is refused before it reaches
# SQLite, which keeps DDL/DML out of the logs even though the read-only
# connection would have rejected it anyway.
_ALLOWED_LEADING = ("select", "with")


@dataclass
class ExecResult:
    """Outcome of one execution attempt."""

    ok: bool
    rows: list[tuple] = field(default_factory=list)
    columns: list[str] = field(default_factory=list)
    error: str | None = None
    # "rejected" | "timeout" | "sql_error" | "db_error"; None when ok
    error_kind: str | None = None
    truncated: bool = False
    elapsed_s: float = 0.0

    def __bool__(self) -> bool:
        # lets callers write `if result:` instead of `if result.ok:`
        return self.ok


def strip_sql_comments(sql: str) -> str:
    """Remove -- and /* */ comments without touching string literals.

    The guard below inspects the leading keyword, so a model that emits a
    comment line followed by DROP TABLE must not be mistaken for a comment.
    Quoted text is preserved verbatim so a literal containing -- survives.
    """
    out: list[str] = []
    i, n = 0, len(sql)
    quote: str | None = None
    quote_chars = ("'", '"', "`")
    while i < n:
        ch = sql[i]
        if quote:
            out.append(ch)
            if ch == quote:
                # a doubled quote is an escaped quote inside the literal,
                # not the end of it
                if i + 1 < n and sql[i + 1] == quote:
                    out.append(sql[i + 1])
                    i += 2
                    continue
                quote = None
            i += 1
        elif ch in quote_chars:
            quote = ch
            out.append(ch)
            i += 1
        elif ch == "-" and sql.startswith("--", i):
            while i < n and sql[i] != "\n":
                i += 1
        elif ch == "/" and sql.startswith("/*", i):
            end = sql.find("*/", i + 2)
            i = n if end == -1 else end + 2
            out.append(" ")
        else:
            out.append(ch)
            i += 1
    return "".join(out)


def is_read_only(sql: str) -> bool:
    """True if `sql` is a single SELECT/WITH statement."""
    cleaned = strip_sql_comments(sql).strip().rstrip(";").strip()
    if not cleaned:
        return False
    if ";" in cleaned:  # a second statement was appended
        return False
    return cleaned.split(None, 1)[0].lower() in _ALLOWED_LEADING


def _connect(path: Path) -> sqlite3.Connection:
    """Open `path` read-only.

    Path.as_uri() rather than an f-string: on Windows a raw path produces
    a URI that SQLite reads as a *relative* path named after the drive letter,
    and the colon plus any spaces need percent-encoding.
    """
    conn = sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True, check_same_thread=False)
    conn.text_factory = lambda b: b.decode("utf-8", errors="replace")
    return conn


def _run(path: Path, sql: str, timeout_s: float, out: queue.Queue) -> None:
    """Worker body: connect, execute, push one ExecResult onto `out`."""
    started = time.monotonic()
    deadline = started + timeout_s
    conn = None
    try:
        conn = _connect(path)
        # Fires every 10k VDBE instructions; a non-zero return aborts the query.
        # This is what actually stops a runaway cartesian product -- the thread
        # in execute_sql() only bounds how long *we* wait, since Python threads
        # cannot be killed from the outside.
        conn.set_progress_handler(lambda: 1 if time.monotonic() > deadline else 0, 10_000)

        cur = conn.execute(sql)  # sqlite3 refuses multi-statement strings here
        columns = [d[0] for d in cur.description] if cur.description else []
        rows = cur.fetchmany(MAX_ROWS + 1)
        truncated = len(rows) > MAX_ROWS
        out.put(
            ExecResult(
                ok=True,
                rows=[tuple(r) for r in rows[:MAX_ROWS]],
                columns=columns,
                truncated=truncated,
                elapsed_s=time.monotonic() - started,
            )
        )
    except sqlite3.OperationalError as exc:
        # The progress handler surfaces as OperationalError("interrupted").
        timed_out = "interrupt" in str(exc).lower() or time.monotonic() > deadline
        out.put(
            ExecResult(
                ok=False,
                error="query exceeded timeout" if timed_out else str(exc),
                error_kind="timeout" if timed_out else "sql_error",
                elapsed_s=time.monotonic() - started,
            )
        )
    except sqlite3.Error as exc:
        out.put(
            ExecResult(
                ok=False,
                error=f"{type(exc).__name__}: {exc}",
                error_kind="sql_error",
                elapsed_s=time.monotonic() - started,
            )
        )
    except Exception as exc:  # decoding / conversion faults inside the driver
        out.put(
            ExecResult(
                ok=False,
                error=f"{type(exc).__name__}: {exc}",
                error_kind="db_error",
                elapsed_s=time.monotonic() - started,
            )
        )
    finally:
        if conn is not None:
            conn.close()


def execute_sql(
    db: str | Path,
    sql: str,
    timeout_s: float = DEFAULT_TIMEOUT_S,
    enforce_read_only: bool = True,
) -> ExecResult:
    """Execute `sql` against a database, read-only and time-bounded.

    `db` is either a db_id (resolved through config) or a path to a .sqlite
    file. Never raises for bad SQL -- failures come back as ExecResult(ok=False)
    so the evaluation loop can score them instead of crashing.
    """
    path = Path(db) if str(db).endswith(".sqlite") else config.db_path(str(db))
    if not path.exists():
        return ExecResult(ok=False, error=f"database not found: {path}", error_kind="db_error")

    if enforce_read_only and not is_read_only(sql):
        return ExecResult(
            ok=False,
            error="only a single SELECT/WITH statement is allowed",
            error_kind="rejected",
        )

    out: queue.Queue = queue.Queue(maxsize=1)
    worker = threading.Thread(target=_run, args=(path, sql, timeout_s, out), daemon=True)
    started = time.monotonic()
    worker.start()
    # +2s of slack: the progress handler should have aborted the query by the
    # deadline, so reaching this branch means the time went somewhere SQLite
    # does not step through (opening the file, materialising one huge row).
    worker.join(timeout_s + 2.0)
    if worker.is_alive():
        return ExecResult(
            ok=False,
            error="query exceeded timeout",
            error_kind="timeout",
            elapsed_s=time.monotonic() - started,
        )
    return out.get_nowait()


def describe(result: ExecResult, max_rows: int = 5) -> str:
    """One-screen summary of a result, for CLI output and repair prompts."""
    if not result.ok:
        return f"[{result.error_kind}] {result.error}"
    head = " | ".join(result.columns) if result.columns else "(no columns)"
    body = "\n".join("  " + " | ".join(repr(v) for v in row) for row in result.rows[:max_rows])
    extra = len(result.rows) - max_rows
    more = f"\n  ... {extra} more rows" if extra > 0 else ""
    flag = " (truncated)" if result.truncated else ""
    return f"{len(result.rows)} row(s){flag}\n  {head}\n{body}{more}"
