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


def load_dev(limit: int | None = None) -> list[Example]:
    """The 1034-question dev set (20 databases), optionally truncated.

    Pass `limit=config.DEV_TUNING_SLICE` while iterating on prompts; report
    final numbers with no limit.
    """
    examples = _load(config.DEV_JSON)
    return examples[:limit] if limit else examples


def load_train(limit: int | None = None) -> list[Example]:
    """The 7000-question train split, used only as a few-shot example pool."""
    examples = _load(config.TRAIN_JSON)
    return examples[:limit] if limit else examples
