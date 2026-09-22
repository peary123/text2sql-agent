"""Aggregate the hand-written error labels into the table that drives the roadmap.

Reads `eval/error_labels.json`, cross-checks it against the sample it claims to
label, and prints the category breakdown plus how each category maps onto the
evaluator's own machine-assigned reason.

That cross-tab is the useful part: `value_mismatch` is 80% of the evaluator's
failure reasons and tells you nothing about what to fix, because it covers
everything from a mis-cased string literal to a wrong join. The hand labels are
what split it into things with different fixes.

Usage:
    python scripts/06_error_report.py
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# What each category is evidence for. Written down here so the link between an
# observation and the change it justifies is explicit, and so a category that
# justifies nothing is visible as such.
FIXES = {
    "column_order": "instruction: order columns as the question names them (74%)",
    "extra_column": "instruction: return only the columns the question asks for",
    "wrong_value": "sample values in the schema prompt",
    "wrong_column": "sample values in the schema prompt",
    "wrong_join": "foreign keys stated explicitly in the prompt",
    "query_logic": "self-consistency (sample several, vote on the result)",
    "distinct": "few-shot examples (retrieved from questions of the same shape)",
    "sql_error": "execute-and-repair loop",
    "aggregation": "few-shot examples (retrieved from questions of the same shape)",
    "ambiguous_question": "nothing -- the question does not determine one answer",
    "gold_debatable": "nothing -- the benchmark's answer is the questionable one",
}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--labels", default="eval/error_labels.json")
    parser.add_argument("--sample", default="results/error_sample.jsonl")
    args = parser.parse_args()

    raw = json.loads((ROOT / args.labels).read_text(encoding="utf-8"))
    labels = {k: v for k, v in raw.items() if not k.startswith("_")}
    sample = [json.loads(line) for line in (ROOT / args.sample).open(encoding="utf-8")]
    by_qid = {r["qid"]: r for r in sample}

    # A label file that has drifted from the sample it describes is worse than
    # no label file, so mismatches are an error rather than a warning.
    missing = [qid for qid in by_qid if qid not in labels]
    extra = [qid for qid in labels if qid not in by_qid]
    if missing or extra:
        if missing:
            print(f"unlabelled ({len(missing)}): {', '.join(missing[:5])}")
        if extra:
            print(f"labelled but not in the sample ({len(extra)}): {', '.join(extra[:5])}")
        return 1

    total = len(labels)
    counts = Counter(label for label, _ in labels.values())
    cross: dict[str, Counter] = defaultdict(Counter)
    for qid, (label, _) in labels.items():
        cross[label][by_qid[qid]["reason"]] += 1

    print(f"{total} failures labelled by hand\n")
    print(f"| {'category':<20} | {'n':>2} | {'%':>5} | what it is evidence for |")
    print(f"|{'-' * 22}|{'-' * 4}|{'-' * 7}|{'-' * 56}|")
    for label, n in counts.most_common():
        print(f"| {label:<20} | {n:>2} | {n / total:>5.0%} | {FIXES.get(label, '?'):<54} |")

    actionable = sum(n for label, n in counts.items()
                     if label not in ("gold_debatable", "ambiguous_question"))
    print(f"\nactionable: {actionable}/{total} ({actionable / total:.0%})")
    print(f"not the model's mistake: {total - actionable}/{total} "
          f"({(total - actionable) / total:.0%})")

    print("\nhand label vs the evaluator's own reason:\n")
    reasons = sorted({r["reason"] for r in sample})
    width = max(len(label) for label in counts) + 1
    print(" " * width + "  " + "  ".join(f"{r[:14]:>14}" for r in reasons))
    for label, _ in counts.most_common():
        cells = "  ".join(f"{cross[label][r] or '':>14}" for r in reasons)
        print(f"{label:<{width}}  {cells}")

    # The single most useful number for deciding what to build next.
    n_failures = 290
    print(f"\nprojected onto all {n_failures} baseline failures:")
    for label, n in counts.most_common(5):
        print(f"  {label:<20} ~{round(n / total * n_failures):>3} questions")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
