"""Tests for the tuning-slice sampler.

The slice decides which questions every prompt change is judged on, so if it is
unrepresentative the whole ablation is measuring the wrong thing.
"""

from __future__ import annotations

import collections
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.dataset import Example, stratified_sample  # noqa: E402


def _fake(counts: dict[str, int]) -> list[Example]:
    out, i = [], 0
    for db, n in counts.items():
        for j in range(n):
            out.append(Example(index=i, db_id=db, question=f"q{j}", gold_sql="SELECT 1"))
            i += 1
    return out


def test_sample_has_the_requested_size() -> None:
    pool = _fake({"a": 500, "b": 300, "c": 200})
    assert len(stratified_sample(pool, 100)) == 100


def test_every_database_is_represented() -> None:
    """A database must not drop out of the tuning loop just for being small."""
    pool = _fake({"big": 900, "tiny": 4})
    dbs = {ex.db_id for ex in stratified_sample(pool, 50)}
    assert dbs == {"big", "tiny"}


def test_proportions_track_the_full_set() -> None:
    pool = _fake({"a": 600, "b": 300, "c": 100})
    sample = stratified_sample(pool, 100)
    got = collections.Counter(ex.db_id for ex in sample)
    for db, n in {"a": 600, "b": 300, "c": 100}.items():
        assert abs(got[db] / 100 - n / 1000) < 0.03, (db, got[db])


def test_sampling_is_deterministic() -> None:
    """Two configurations must be compared on identical questions."""
    pool = _fake({"a": 500, "b": 300, "c": 200})
    assert stratified_sample(pool, 77) == stratified_sample(pool, 77)


def test_original_order_is_preserved() -> None:
    pool = _fake({"a": 50, "b": 50})
    sample = stratified_sample(pool, 20)
    assert [ex.index for ex in sample] == sorted(ex.index for ex in sample)


def test_no_duplicates() -> None:
    pool = _fake({"a": 50, "b": 50, "c": 10})
    sample = stratified_sample(pool, 40)
    assert len({ex.qid for ex in sample}) == len(sample)


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
