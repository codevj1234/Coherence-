"""Regenerate the final report's figures and numbers from the real-model step logs.

    python -m agent.analysis.report_figures                       # logs in guard/results_real, figures in docs/figures
    python -m agent.analysis.report_figures --runs path/to/results_real --out docs/figures

Definitions (one place, so the report and the figures cannot drift apart):
- committed regression: a step whose committed edit broke an earlier policy (outcome REGRESSION or BOTH and
  committed). Rejected attempts under C3 are never committed, so they are not regressions.
- first regression step: the step of the first committed regression in a sequence; none = censored at the
  sequence length.
- survival at step k: share of sequences with no committed regression up to and including step k.
- regression half-life: median first-regression step over sequences; reported as "> L" when fewer than half of
  the sequences regressed within their L steps.
- broken policies at step k: `violated_now`, the number of tracked policies broken after step k.
"""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MODELS = [("qwen2.5-coder-7b", "Qwen2.5-Coder-7B"), ("llama3.1-8b", "Llama-3.1-8B-Instruct")]
COND_COLOR = {"C0": "#d9534f", "C1": "#f0ad4e", "C2": "#337ab7", "C3": "#5cb85c"}
TYPES = ["acl_permit", "acl_deny", "prefix_filter", "vrf_isolate", "waypoint"]


def load_runs(runs_dir: Path) -> dict:
    """{(model, condition): {sequence_id: [step records sorted by step]}}"""
    out: dict = defaultdict(dict)
    for f in sorted(runs_dir.glob("runs/*/*/*/*.jsonl")):
        model, _bed, cond = f.parts[-4:-1]
        recs = [json.loads(line) for line in f.read_text().splitlines() if line.strip()]
        out[(model, cond)][f.stem] = sorted(recs, key=lambda r: r["step"])
    return out


def is_committed_regression(r: dict) -> bool:
    return bool(r.get("committed")) and r.get("outcome") in ("REGRESSION", "BOTH")


def summarize(seqs: dict) -> dict:
    steps = [r for s in seqs.values() for r in s]
    n = len(steps)
    firsts, lengths = [], []
    for s in seqs.values():
        firsts.append(next((r["step"] for r in s if is_committed_regression(r)), None))
        lengths.append(len(s))
    regressed = sorted(f for f in firsts if f is not None)
    if len(regressed) * 2 > len(firsts) or (len(firsts) % 2 == 0 and len(regressed) * 2 == len(firsts)):
        times = sorted(f if f is not None else 10 ** 6 for f in firsts)
        m = len(times) // 2
        half = times[m] if len(times) % 2 else (times[m - 1] + times[m]) / 2
        half_life = f"{half:g}"
    else:
        half_life = f"> {max(lengths)}"
    by_type = defaultdict(lambda: [0, 0])
    for r in steps:
        by_type[r["intent_type"]][1] += 1
        by_type[r["intent_type"]][0] += r["outcome"] == "SUCCESS"
    return dict(
        sequences=len(seqs), steps=n,
        success=sum(r["outcome"] == "SUCCESS" for r in steps),
        regressions=sum(is_committed_regression(r) for r in steps),
        regressed_sequences=len(regressed),
        broken_at_end=[s[-1]["violated_now"] for s in seqs.values()],
        rejected=sum(r["outcome"] == "REJECTED" for r in steps),
        parse_fail_final=sum(r.get("attempt_outcome") == "PARSE_FAIL" for r in steps),
        first_attempt_regression=sum(bool(r.get("first_attempt_regression")) for r in steps),
        tokens_in=round(sum(r["tokens_in"] for r in steps) / n),
        retries=round(sum(r["retries"] for r in steps) / n, 2),
        first_regression_steps=firsts, half_life=half_life,
        by_type={t: tuple(v) for t, v in by_type.items()},
    )


def mean_broken(seqs: dict, length: int) -> list[float]:
    return [sum(s[k]["violated_now"] for s in seqs.values()) / len(seqs) for k in range(length)]


def survival(seqs: dict, length: int) -> list[float]:
    firsts = [next((r["step"] for r in s if is_committed_regression(r)), None) for s in seqs.values()]
    return [sum(1 for f in firsts if f is None or f > k) / len(firsts) for k in range(1, length + 1)]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", type=Path, default=ROOT / "guard" / "results_real")
    ap.add_argument("--out", type=Path, default=ROOT / "docs" / "figures")
    a = ap.parse_args()
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    runs = load_runs(a.runs)
    a.out.mkdir(parents=True, exist_ok=True)
    stats = {f"{m}/{c}": summarize(seqs) for (m, c), seqs in sorted(runs.items())}
    (a.out / "report_numbers.json").write_text(json.dumps(stats, indent=1))
    present = [(m, label, sorted(c for (mm, c) in runs if mm == m)) for m, label in MODELS]
    present = [p for p in present if p[2]]
    L = max(len(s) for seqs in runs.values() for s in seqs.values())
    steps = list(range(1, L + 1))

    # Figure 1: committed regressions per condition
    fig, axes = plt.subplots(1, len(present), figsize=(5.5 * len(present), 4), sharey=True)
    for ax, (m, label, conds) in zip(axes, present):
        vals = [stats[f"{m}/{c}"]["regressions"] for c in conds]
        n = [stats[f"{m}/{c}"]["steps"] for c in conds]
        bars = ax.bar(conds, vals, color=[COND_COLOR[c] for c in conds], edgecolor="black")
        for b, v, k in zip(bars, vals, n):
            ax.text(b.get_x() + b.get_width() / 2, v + 0.1, f"{v} of {k}", ha="center", fontsize=10)
        ax.set_title(label, fontweight="bold")
        ax.set_xlabel("Condition")
        ax.grid(axis="y", ls="--", alpha=0.5)
    axes[0].set_ylabel("Committed regressions")
    axes[0].set_ylim(0, max(v["regressions"] for v in stats.values()) + 1.2)
    fig.tight_layout()
    fig.savefig(a.out / "regression_rates.png", dpi=150)
    plt.close(fig)

    # Figure 2: mean broken policies after each step
    fig, axes = plt.subplots(1, len(present), figsize=(5.5 * len(present), 4), sharey=True)
    for ax, (m, label, conds) in zip(axes, present):
        for c in conds:
            ax.plot(steps, mean_broken(runs[(m, c)], L), marker="o", color=COND_COLOR[c], label=c)
        ax.set_title(label, fontweight="bold")
        ax.set_xlabel("Step")
        ax.grid(ls="--", alpha=0.5)
        ax.legend()
    axes[0].set_ylabel("Policies broken after the step (mean of 3 sequences)")
    fig.tight_layout()
    fig.savefig(a.out / "cumulative_broken_rules.png", dpi=150)
    plt.close(fig)

    # Figure 3: survival (share of sequences with no committed regression yet) + half-life per condition
    fig, axes = plt.subplots(1, len(present), figsize=(5.5 * len(present), 4), sharey=True)
    for ax, (m, label, conds) in zip(axes, present):
        for i, c in enumerate(conds):
            st = stats[f"{m}/{c}"]
            ax.step([0] + steps, [1.0] + survival(runs[(m, c)], L), where="post", color=COND_COLOR[c],
                    lw=2.5, alpha=0.85, label=f"{c} (half-life {st['half_life']} steps)",
                    zorder=3 + i, linestyle=["-", "--", "-.", ":"][i % 4])
        ax.axhline(0.5, color="grey", ls=":", lw=1)
        ax.set_ylim(-0.05, 1.08)
        ax.set_title(label, fontweight="bold")
        ax.set_xlabel("Step")
        ax.grid(ls="--", alpha=0.5)
        ax.legend(loc="lower left", fontsize=9)
    axes[0].set_ylabel("Share of sequences with no regression yet")
    fig.tight_layout()
    fig.savefig(a.out / "survival_and_halflife.png", dpi=150)
    plt.close(fig)

    # Figure 4: success by intent type
    fig, axes = plt.subplots(1, len(present), figsize=(5.5 * len(present), 4), sharey=True)
    for ax, (m, label, conds) in zip(axes, present):
        w = 0.8 / len(conds)
        for i, c in enumerate(conds):
            bt = stats[f"{m}/{c}"]["by_type"]
            ys = [bt[t][0] / bt[t][1] * 100 if t in bt else 0 for t in TYPES]
            xs = [k + (i - (len(conds) - 1) / 2) * w for k in range(len(TYPES))]
            ax.bar(xs, ys, w, color=COND_COLOR[c], edgecolor="black", label=c)
        ax.set_xticks(range(len(TYPES)))
        ax.set_xticklabels([t.replace("_", "\n") for t in TYPES])
        ax.set_title(label, fontweight="bold")
        ax.grid(axis="y", ls="--", alpha=0.5)
        ax.legend()
    axes[0].set_ylabel("Edits that succeeded (%)")
    fig.tight_layout()
    fig.savefig(a.out / "success_by_intent_type.png", dpi=150)
    plt.close(fig)

    for k, v in stats.items():
        print(k, {x: v[x] for x in ("success", "regressions", "broken_at_end", "rejected", "half_life", "tokens_in")})


if __name__ == "__main__":
    main()
