"""Loading Spider's question files.

Spider ships three splits. We use `dev.json` for evaluation and
`train_spider.json` as the pool that few-shot examples are retrieved from.
Keeping those disjoint is the whole point of the split, so nothing in this
module ever mixes them.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from . import config


@dataclass(frozen=True)
class Example:
    """One natural-language question paired with its gold SQL."""

    index: int
    db_id: str
    question: str
    gold_sql: str

    @property
    def qid(self) -> str:
        """Stable identifier used in result files."""
        return f"{self.db_id}#{self.index}"


def _load(path: Path) -> list[Example]:
    if not path.exists():
        config.require_data()
        raise FileNotFoundError(path)
    with path.open(encoding="utf-8") as fh:
        raw = json.load(fh)
    return [
        Example(
            index=i,
            db_id=row["db_id"],
            question=row["question"].strip(),
            # Spider calls the gold SQL "query"; a few rows have trailing
            # whitespace/newlines that confuse string comparison downstream.
            gold_sql=row["query"].strip(),
        )
        for i, row in enumerate(raw)
    ]


def load_dev(limit: int | None = None, stratified: bool = True) -> list[Example]:
    """The dev set, or a subset of it to iterate on.

    `limit` returns a *stratified* subset by default, not the first N. dev.json
    is ordered by database, so `examples[:200]` covers 4 of the 20 databases and
    is 92/200 `car_1`, the hardest one -- it scores 64.5% where the full set
    scores 72.0%. Tuning prompts on that is tuning on `car_1`, and nothing says
    whether the gain transfers to the other sixteen databases.
    """
    examples = _load(config.DEV_JSON)
    if not limit or limit >= len(examples):
        return examples
    return stratified_sample(examples, limit) if stratified else examples[:limit]


def stratified_sample(examples: list[Example], n: int) -> list[Example]:
    """`n` examples that mirror the full set's mix of databases.

    Proportional so the subset predicts the whole: a database holding 12% of
    dev contributes ~12% of the sample. Every database gets at least one
    question, so no database can silently drop out of the tuning loop.

    Deterministic -- evenly spaced indices within each database rather than a
    random draw -- so the subset is the same on every machine and every run,
    and two configurations are always compared on identical questions.
    """
    by_db: dict[str, list[Example]] = {}
    for ex in examples:
        by_db.setdefault(ex.db_id, []).append(ex)

    total = len(examples)
    quota = {db: max(1, round(n * len(rows) / total)) for db, rows in by_db.items()}

    # Rounding up per database overshoots; trim from the largest quotas so the
    # result is exactly n and the small databases keep their single question.
    while sum(quota.values()) > n:
        biggest = max(quota, key=lambda db: (quota[db], db))
        if quota[biggest] <= 1:
            break
        quota[biggest] -= 1

    chosen: list[Example] = []
    for db, rows in by_db.items():
        k = min(quota[db], len(rows))
        step = len(rows) / k
        chosen.extend(rows[int(i * step)] for i in range(k))

    # Keep the original dev order so result files line up between runs.
    order = {ex.qid: i for i, ex in enumerate(examples)}
    return sorted(chosen, key=lambda ex: order[ex.qid])


def load_train(limit: int | None = None) -> list[Example]:
    """The 7000-question train split, used only as a few-shot example pool."""
    examples = _load(config.TRAIN_JSON)
    return examples[:limit] if limit else examples
