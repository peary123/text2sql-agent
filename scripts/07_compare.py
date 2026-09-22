"""Compare two runs on the same questions, and say whether the difference is real.

An ablation table full of one-point differences is worth nothing if one point is
inside the noise. On 200 questions at ~75% accuracy the standard error is about
3 points, so two configurations can differ by 3 points and be indistinguishable.

Because both runs answer the *same* questions, the comparison is paired, and the
right test is McNemar's: of the questions where the two runs disagree, how
lopsided is the split? Questions both runs get right, or both get wrong, carry
no information about which is better and are excluded. That is much more
sensitive than comparing two independent accuracy figures.

The p-value is exact (a two-sided binomial test at p=0.5), not the chi-square
approximation, because the discordant counts here are often small.

Usage:
    python scripts/07_compare.py results/baseline_200.jsonl results/base+colorder_200.jsonl
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def load(path: Path) -> dict[str, dict]:
    return {json.loads(line)["qid"]: json.loads(line) for line in path.open(encoding="utf-8")}


def exact_mcnemar(b: int, c: int) -> float:
    """Two-sided exact p-value for a McNemar table with discordant counts b, c.

    Under the null, each discordant question is a fair coin: it could have gone
    either way. So the p-value is the two-sided binomial tail at p=0.5 over the
    b + c discordant questions.
    """
    n = b + c
    if n == 0:
        return 1.0
    k = min(b, c)
    tail = sum(math.comb(n, i) for i in range(k + 1)) / (2**n)
    return min(1.0, 2 * tail)


def wilson(correct: int, total: int, z: float = 1.96) -> tuple[float, float]:
    """Wilson 95% interval — behaves sensibly near 0 and 1, unlike the normal one."""
    if total == 0:
        return (0.0, 0.0)
    p = correct / total
    denom = 1 + z**2 / total
    centre = (p + z**2 / (2 * total)) / denom
    half = z * math.sqrt(p * (1 - p) / total + z**2 / (4 * total**2)) / denom
    return (max(0.0, centre - half), min(1.0, centre + half))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("before")
    parser.add_argument("after")
    parser.add_argument("--show", type=int, default=4, help="example flips to print")
    args = parser.parse_args()

    before, after = load(ROOT / args.before), load(ROOT / args.after)
    shared = sorted(set(before) & set(after))
    if len(shared) != len(before) or len(shared) != len(after):
        print(f"note: comparing the {len(shared)} questions both runs contain "
              f"({len(before)} vs {len(after)})")

    b_ok = sum(before[q]["correct"] for q in shared)
    a_ok = sum(after[q]["correct"] for q in shared)
    n = len(shared)

    gained = [q for q in shared if not before[q]["correct"] and after[q]["correct"]]
    lost = [q for q in shared if before[q]["correct"] and not after[q]["correct"]]

    print(f"questions: {n}\n")
    for name, ok in ((Path(args.before).stem, b_ok), (Path(args.after).stem, a_ok)):
        lo, hi = wilson(ok, n)
        print(f"  {name:<34} {ok / n:6.1%}  ({ok}/{n})   95% CI [{lo:.1%}, {hi:.1%}]")

    delta = (a_ok - b_ok) / n
    p = exact_mcnemar(len(lost), len(gained))
    print(f"\n  difference : {delta:+.1%}  ({a_ok - b_ok:+d} questions)")
    print(f"  fixed      : {len(gained)}")
    print(f"  broken     : {len(lost)}")
    print(f"  unchanged  : {n - len(gained) - len(lost)}  (carry no information)")
    print(f"  McNemar exact p = {p:.3f}", end="  ")
    if p < 0.05:
        print("-> the difference is unlikely to be noise")
    else:
        print("-> NOT distinguishable from noise on this many questions")

    for label, qids in (("fixed", gained), ("broken", lost)):
        if not qids:
            continue
        print(f"\n{label}:")
        for qid in qids[: args.show]:
            record = after[qid] if label == "fixed" else before[qid]
            print(f"  {qid}  [{before[qid]['reason']} -> {after[qid]['reason']}]")
            print(f"    Q: {record['question'][:88]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
