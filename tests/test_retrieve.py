"""Tests for few-shot retrieval.

The property that matters is not "it returns something similar" but that it
returns the same thing every time and never leaks the evaluation set.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src import config, dataset  # noqa: E402
from src.retrieve import FewShotIndex  # noqa: E402

HAVE_DATA = config.DEV_JSON.exists()


def _toy_index() -> FewShotIndex:
    rows = [
        ("How many heads are older than 56?", "SELECT count(*) FROM head WHERE age > 56"),
        ("How many reviews does each director get?", "SELECT count(*), director FROM m GROUP BY director"),
        ("List the name and age of every student.", "SELECT name, age FROM student"),
        ("What is the average age for each gender?", "SELECT avg(age), gender FROM p GROUP BY gender"),
        ("Show all the cities in France.", "SELECT name FROM city WHERE country = 'France'"),
        ("How many cities are in each country?", "SELECT count(*), country FROM city GROUP BY country"),
    ]
    examples = [
        dataset.Example(index=i, db_id=f"db{i}", question=q, gold_sql=s)
        for i, (q, s) in enumerate(rows)
    ]
    return FewShotIndex(examples)


def test_retrieval_is_deterministic() -> None:
    """Two ablation rows must differ by the configuration, not by the draw."""
    index = _toy_index()
    q = "How many books does each author have?"
    assert [e.qid for e in index.search(q, k=3)] == [e.qid for e in index.search(q, k=3)]


def test_closest_example_sits_nearest_the_question() -> None:
    index = _toy_index()
    results = index.search_with_scores("How many books does each author have?", k=3)
    scores = [s for s, _ in results]
    assert scores == sorted(scores), "results should be ordered worst-first"


def test_matches_question_shape_not_topic() -> None:
    """Across disjoint schemas a topic match is useless; the shape is the signal."""
    index = _toy_index()
    best = index.search("How many albums does each label release?", k=1)[0]
    assert "each" in best.question.lower()
    assert "GROUP BY" in best.gold_sql


def test_k_is_respected_and_zero_disables() -> None:
    index = _toy_index()
    assert len(index.search("anything", k=2)) == 2
    assert index.search("anything", k=0) == []
    # asking for more than the pool holds must not raise
    assert len(index.search("anything", k=99)) == 6


def test_pool_never_contains_dev_questions() -> None:
    """The whole point of the split: a dev answer must not be retrievable."""
    if not HAVE_DATA:
        return
    train_dbs = {e.db_id for e in dataset.load_train()}
    dev_dbs = {e.db_id for e in dataset.load_dev()}
    assert not (train_dbs & dev_dbs), sorted(train_dbs & dev_dbs)


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
