"""Metrics over step-record JSONL (Phase 3 step 7 / Phase 7)."""
from __future__ import annotations

import json
import random
import statistics
from collections import defaultdict
from typing import Dict, List, Tuple


def load(path: str) -> List[dict]:
    with open(path) as f:
        return [json.loads(l) for l in f]


def group_runs(recs: List[dict]) -> Dict[Tuple[str, str, str], Dict[str, List[dict]]]:
    g: Dict = defaultdict(lambda: defaultdict(list))
    for r in recs:
        g[(r["model"], r["condition"], r["mode"])][r["sequence_id"]].append(r)
    for k in g:
        for s in g[k]:
            g[k][s].sort(key=lambda r: r["step"])
    return g


def committed_regression(r: dict) -> bool:
    return bool(r["broken_invariants"]) and r["committed"]


def first_regression_step(run: List[dict], n: int) -> int:
    """Step of the first committed regression; n+1 if the run never regressed (right-censored)."""
    for r in run:
        if committed_regression(r):
            return r["step"]
    return n + 1


def half_life(first_steps: List[int], n: int) -> Tuple[float, bool]:
    med = statistics.median(first_steps)
    return med, med > n                      # (value, censored)


def bootstrap_ci(values: List[float], stat=statistics.median, b: int = 2000, seed: int = 0, alpha: float = 0.05):
    rng = random.Random(seed)
    stats = sorted(stat([rng.choice(values) for _ in values]) for _ in range(b))
    return stats[int(alpha / 2 * b)], stats[int((1 - alpha / 2) * b) - 1]


def decay_curve(runs: Dict[str, List[dict]], n: int) -> List[float]:
    seqs = list(runs)
    return [sum(runs[s][k]["violated_now"] for s in seqs) / len(seqs) for k in range(n)]


def summarize(runs: Dict[str, List[dict]], n: int) -> dict:
    recs = [r for run in runs.values() for r in run]
    firsts = [first_regression_step(run, n) for run in runs.values()]
    hl, cens = half_life(firsts, n)
    lo, hi = bootstrap_ci(firsts)
    newly = sum(len(r["broken_invariants"]) for r in recs)
    return dict(
        sequences=len(runs), steps=len(recs),
        half_life=hl, half_life_censored=cens, half_life_ci=(lo, hi),
        runs_with_regression=sum(f <= n for f in firsts),
        regression_step_rate=sum(committed_regression(r) for r in recs) / len(recs),
        final_violated_mean=sum(run[-1]["violated_now"] for run in runs.values()) / len(runs),
        completion_rate=sum(r["outcome"] == "SUCCESS" for r in recs) / len(recs),
        rejected_rate=sum(r["outcome"] == "REJECTED" for r in recs) / len(recs),
        parse_fail_rate=sum(r["attempt_outcome"] == "PARSE_FAIL" for r in recs) / len(recs),
        forward_fail_rate=sum(r["attempt_outcome"] in ("FORWARD_FAIL", "BOTH") for r in recs) / len(recs),
        first_attempt_regression_rate=sum(r["first_attempt_regression"] for r in recs) / len(recs),
        mean_retries=sum(r["retries"] for r in recs) / len(recs),
        tokens_in_per_step=sum(r["tokens_in"] for r in recs) / len(recs),
        tokens_out_per_step=sum(r["tokens_out"] for r in recs) / len(recs),
        dep_map_miss_rate=(sum(r["dep_map_miss"] for r in recs) / newly) if newly else None,
        newly_broken_total=newly,
    )


def breakdown(runs: Dict[str, List[dict]], key: str) -> Dict[str, Tuple[int, float]]:
    """First-attempt regression rate by a step attribute (trap_kind / intent_type)."""
    cnt, bad = defaultdict(int), defaultdict(int)
    for run in runs.values():
        for r in run:
            k = str(r[key])
            cnt[k] += 1
            bad[k] += r["first_attempt_regression"]
    return {k: (cnt[k], bad[k] / cnt[k]) for k in sorted(cnt)}


def paired_bootstrap(a: Dict[str, float], b: Dict[str, float], b_iter: int = 5000, seed: int = 0, alpha: float = 0.05):
    """Paired bootstrap over sequences: a, b = {sequence_id: value} for two conditions run on the SAME sequences.
    Returns (mean difference b - a, (lo, hi) CI, two-sided p) where p = 2 * min(P(diff <= 0), P(diff >= 0))."""
    ids = sorted(set(a) & set(b))
    if not ids:
        return None, (None, None), None
    d = [b[i] - a[i] for i in ids]
    rng = random.Random(seed)
    stats = sorted(sum(rng.choice(d) for _ in d) / len(d) for _ in range(b_iter))
    lo, hi = stats[int(alpha / 2 * b_iter)], stats[int((1 - alpha / 2) * b_iter) - 1]
    p = 2 * min(sum(s <= 0 for s in stats), sum(s >= 0 for s in stats)) / b_iter
    return sum(d) / len(d), (lo, hi), min(1.0, p)


def per_sequence(runs: Dict[str, List[dict]], n: int) -> Dict[str, Dict[str, float]]:
    """Per-sequence values used for paired comparisons."""
    out = {}
    for sid, run in runs.items():
        out[sid] = dict(
            first_regression_step=first_regression_step(run, n),
            final_violated=run[-1]["violated_now"],
            completion=sum(r["outcome"] == "SUCCESS" for r in run) / len(run),
            tokens_in=sum(r["tokens_in"] for r in run) / len(run),
        )
    return out
