"""Retrieving few-shot examples from the training split.

Spider's train and dev splits share no databases -- 140 against 20, no overlap.
So a retrieved example can never tell the model which table to join or what a
column is called: its table names are useless here.

What it *can* carry is the mapping from a question's shape to a query's shape.
That matters because the largest remaining error class is column order, and
there is no rule to state: gold follows the question's word order only 74% of
the time. A convention that cannot be described can still be demonstrated.

Which is why similarity is computed over word 1- and 2-grams with stop words
kept. "how many", "for each", "list the" are the signal here; the content words
-- singer, country, stadium -- are noise, because the example's schema is not
the one being queried.
"""

from __future__ import annotations

import functools

from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import linear_kernel

from . import dataset


class FewShotIndex:
    """TF-IDF index over the training questions.

    TF-IDF rather than embeddings on purpose: it is a few lines, it needs no
    model download or API call, and it is exactly reproducible. If it turns out
    to be the bottleneck, that is a measured reason to replace it, which is a
    better reason than starting with the heavier thing.
    """

    def __init__(self, examples: list[dataset.Example] | None = None) -> None:
        self.examples = examples if examples is not None else dataset.load_train()
        self.vectorizer = TfidfVectorizer(
            ngram_range=(1, 2),
            lowercase=True,
            sublinear_tf=True,
            # Deliberately no stop-word list: "how many" and "for each" are the
            # part of the question that predicts the query's shape.
            stop_words=None,
            min_df=2,
        )
        self.matrix = self.vectorizer.fit_transform(e.question for e in self.examples)

    def search(self, question: str, k: int = 4) -> list[dataset.Example]:
        """The k most similar training questions, most similar last.

        Returned in ascending order of similarity so that the closest example
        sits nearest the question in the prompt.

        Ties are broken by position in the training file, so the same question
        always retrieves the same examples on every machine and every run --
        two ablation rows have to differ by the configuration, not by which
        examples happened to come back.
        """
        if k <= 0:
            return []
        query = self.vectorizer.transform([question])
        scores = linear_kernel(query, self.matrix).ravel()
        ranked = sorted(range(len(scores)), key=lambda i: (-scores[i], i))[:k]
        return [self.examples[i] for i in reversed(ranked)]

    def search_with_scores(self, question: str, k: int = 4) -> list[tuple[float, dataset.Example]]:
        """Same as `search`, but keeps the scores -- for inspecting retrieval."""
        if k <= 0:
            return []
        query = self.vectorizer.transform([question])
        scores = linear_kernel(query, self.matrix).ravel()
        ranked = sorted(range(len(scores)), key=lambda i: (-scores[i], i))[:k]
        return [(float(scores[i]), self.examples[i]) for i in reversed(ranked)]


@functools.lru_cache(maxsize=1)
def default_index() -> FewShotIndex:
    """The index over the full training split, built once per process."""
    return FewShotIndex()
