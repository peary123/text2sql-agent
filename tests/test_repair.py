"""Tests for the execute-and-repair loop.

The one that matters most is the first: the loop must not be able to see the
gold query. If it could, every number it produced would be meaningless -- a
system that repairs against the answer key is not a system.

The loop tests use a scripted fake client, so they spend nothing and are exact.
They need the Spider data for a real schema and are skipped without it.
"""

from __future__ import annotations

import inspect
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src import config  # noqa: E402
from src.generate import PromptConfig  # noqa: E402
from src.llm import LLMResponse  # noqa: E402
from src.repair import (  # noqa: E402
    Attempt,
    RepairPolicy,
    build_repair_prompt,
    diagnose,
    generate_with_repair,
    needs_repair,
)

HAVE_DATA = config.DEV_JSON.exists()
DB = "concert_singer"


class ScriptedClient:
    """Returns canned replies in order and records every prompt it was sent."""

    def __init__(self, *replies: str) -> None:
        self.replies = list(replies)
        self.prompts: list[str] = []

    def complete(self, user, system="", temperature=0.0, max_tokens=600, cache_salt=""):
        self.prompts.append(user)
        return LLMResponse(text=self.replies.pop(0), cached=False, model="scripted")


# ------------------------------------------------------------ no gold, ever

def test_repair_loop_cannot_receive_the_gold_query() -> None:
    """Structural guarantee: there is no parameter a gold query could arrive by."""
    for fn in (generate_with_repair, diagnose, build_repair_prompt, needs_repair):
        params = [p.lower() for p in inspect.signature(fn).parameters]
        assert not any("gold" in p for p in params), (fn.__name__, params)


# ----------------------------------------------------------------- policy

def test_default_policy_is_off() -> None:
    assert RepairPolicy().max_repairs == 0
    assert RepairPolicy().tag == ""


def test_errors_are_repaired_empties_only_when_asked() -> None:
    error = Attempt(sql="SELECT x", outcome="sql_error", error="no such column: x")
    empty = Attempt(sql="SELECT 1 WHERE 0", outcome="empty")
    fine = Attempt(sql="SELECT 1", outcome="ok")

    assert needs_repair(error, RepairPolicy(max_repairs=2))
    assert not needs_repair(fine, RepairPolicy(max_repairs=2))
    assert not needs_repair(empty, RepairPolicy(max_repairs=2))
    assert needs_repair(empty, RepairPolicy(max_repairs=2, on_empty=True))


def test_missing_database_is_not_worth_a_repair() -> None:
    """A db_error is the harness's problem; rewording the query cannot fix it."""
    assert not needs_repair(Attempt(sql="SELECT 1", outcome="db_error"), RepairPolicy(max_repairs=2))


# ------------------------------------------------------------------ prompts

def test_repair_prompt_carries_the_failed_query_and_the_error() -> None:
    if not HAVE_DATA:
        return
    failed = Attempt(sql="SELECT Model FROM stadium", outcome="sql_error",
                     error="no such column: Model")
    prompt = build_repair_prompt(DB, "List the stadiums.", PromptConfig(), failed)
    assert "SELECT Model FROM stadium" in prompt
    assert "no such column: Model" in prompt
    assert prompt.rstrip().endswith("-- Query:")


def test_empty_repair_prompt_allows_the_answer_to_stand() -> None:
    """49 dev gold queries return nothing. The model must be allowed to keep one."""
    if not HAVE_DATA:
        return
    failed = Attempt(sql="SELECT Name FROM stadium WHERE 1 = 0", outcome="empty")
    prompt = build_repair_prompt(DB, "List the stadiums.", PromptConfig(), failed)
    assert "repeat the query unchanged" in prompt


# --------------------------------------------------------------- the loop

def test_a_working_query_is_not_touched() -> None:
    if not HAVE_DATA:
        return
    client = ScriptedClient("SELECT Name FROM stadium")
    trace = generate_with_repair(client, DB, "List the stadiums.", PromptConfig(),
                                 RepairPolicy(max_repairs=2))
    assert trace.repairs_used == 0 and trace.llm_calls == 1


def test_an_error_is_fed_back_and_the_fix_is_kept() -> None:
    if not HAVE_DATA:
        return
    client = ScriptedClient("SELECT Nope FROM stadium", "SELECT Name FROM stadium")
    trace = generate_with_repair(client, DB, "List the stadiums.", PromptConfig(),
                                 RepairPolicy(max_repairs=2))
    assert trace.repairs_used == 1
    assert trace.final_sql == "SELECT Name FROM stadium"
    assert "no such column" in client.prompts[1].lower()


def test_the_cap_is_respected() -> None:
    if not HAVE_DATA:
        return
    client = ScriptedClient(*["SELECT Nope FROM stadium"] * 5)
    trace = generate_with_repair(client, DB, "List the stadiums.", PromptConfig(),
                                 RepairPolicy(max_repairs=2))
    assert trace.llm_calls == 3  # the first attempt plus two repairs, no more
    assert len(client.replies) == 2


def test_empty_results_are_left_alone_unless_asked() -> None:
    if not HAVE_DATA:
        return
    q = "SELECT Name FROM stadium WHERE Capacity < 0"
    off = generate_with_repair(ScriptedClient(q), DB, "Tiny stadiums?", PromptConfig(),
                               RepairPolicy(max_repairs=2))
    assert off.repairs_used == 0

    on = generate_with_repair(ScriptedClient(q, q), DB, "Tiny stadiums?", PromptConfig(),
                              RepairPolicy(max_repairs=1, on_empty=True))
    assert on.repairs_used == 1


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
