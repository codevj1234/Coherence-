"""PROVISIONAL: Metric computation and visualization for policy regression benchmarks.

Scored by pure-Python reference oracle (PROVISIONAL - not Batfish).
Computes:
- Per-step success rate
- Cumulative violated invariants against step
- Time to first regression
- Regression half-life (censored at sequence length) with paired bootstrap interval
- Failure breakdown by StepOutcome

Outputs summary CSV and visualization PNG labelled PROVISIONAL.
"""

import argparse
from collections import defaultdict
import csv
from dataclasses import dataclass, field
import json
from pathlib import Path
import random
import sys
from typing import Any

from agent.contract.io import read_jsonl
from agent.contract.models import StepOutcome, StepResult


@dataclass
class SequenceMetrics:
    """Metrics calculated for an individual sequence."""

    sequence_id: str
    length: int
    first_regression_step: int | None  # None if censored
    is_censored: bool
    broken_invariants_by_step: dict[int, int] = field(default_factory=dict)
    outcomes_by_step: dict[int, StepOutcome] = field(default_factory=dict)


@dataclass
class AggregateMetrics:
    """Aggregated evaluation metrics across sequences."""

    total_sequences: int
    total_steps: int
    outcome_counts: dict[str, int]
    outcome_rates: dict[str, float]
    per_step_success: dict[int, float]
    mean_broken_by_step: dict[int, float]
    times_to_first_regression: list[int | None]
    regression_half_life: float | str
    half_life_ci_lower: float | str | None
    half_life_ci_upper: float | str | None
    is_half_life_censored: bool
    provisional_label: str = "PROVISIONAL, reference oracle only"


def _compute_half_life(times: list[int | None], lengths: list[int]) -> tuple[float | str, bool]:
    """Calculate median time to first regression treating non-regressed runs as censored."""
    if not times:
        return 0.0, False

    # Numerical values: use actual step if regressed, else sequence length (censored)
    effective_times = [
        t if t is not None else length for t, length in zip(times, lengths)
    ]
    sorted_times = sorted(effective_times)
    n = len(sorted_times)
    mid = n // 2

    # Check if median step is actually censored (fewer than half regressed)
    regressed_count = sum(1 for t in times if t is not None)
    if regressed_count < (n / 2.0):
        # Censored beyond sequence length
        median_val = sorted_times[mid] if n % 2 != 0 else (sorted_times[mid - 1] + sorted_times[mid]) / 2.0
        return f">{median_val:.1f}", True

    if n % 2 == 1:
        median_val = float(sorted_times[mid])
    else:
        median_val = (sorted_times[mid - 1] + sorted_times[mid]) / 2.0
    return median_val, False


def _bootstrap_half_life(
    times: list[int | None],
    lengths: list[int],
    n_resamples: int = 1000,
    seed: int = 42,
) -> tuple[float | str | None, float | str | None]:
    """Compute paired bootstrap 95% confidence interval for regression half-life."""
    if len(times) < 2:
        return None, None

    rng = random.Random(seed)
    paired = list(zip(times, lengths))
    n = len(paired)
    boot_medians: list[float] = []
    censored_count = 0

    for _ in range(n_resamples):
        sample = [rng.choice(paired) for _ in range(n)]
        sample_times = [s[0] for s in sample]
        sample_lengths = [s[1] for s in sample]
        hl, is_cens = _compute_half_life(sample_times, sample_lengths)
        if is_cens:
            censored_count += 1
            if isinstance(hl, str) and hl.startswith(">"):
                boot_medians.append(float(hl[1:]))
            else:
                boot_medians.append(float(hl))
        else:
            boot_medians.append(float(hl))

    boot_medians.sort()
    low_idx = int(0.025 * n_resamples)
    high_idx = int(0.975 * n_resamples)
    ci_low = boot_medians[low_idx]
    ci_high = boot_medians[high_idx]

    if censored_count > (n_resamples / 2):
        return f">{ci_low:.1f}", f">{ci_high:.1f}"
    return round(ci_low, 2), round(ci_high, 2)


def compute_metrics(records: list[StepResult]) -> AggregateMetrics:
    """Compute aggregate benchmark metrics from StepResult records."""
    seq_records: dict[str, list[StepResult]] = defaultdict(list)
    for r in records:
        seq_records[r.sequence_id].append(r)

    seq_metrics_list: list[SequenceMetrics] = []
    outcome_counts: dict[str, int] = {o.value: 0 for o in StepOutcome}
    step_success_counts: dict[int, int] = defaultdict(int)
    step_total_counts: dict[int, int] = defaultdict(int)
    step_broken_sums: dict[int, int] = defaultdict(int)

    for seq_id, steps in seq_records.items():
        sorted_steps = sorted(steps, key=lambda s: s.step)
        first_reg_step: int | None = None
        broken_by_step: dict[int, int] = {}
        outcomes_by_step: dict[int, StepOutcome] = {}

        for s in sorted_steps:
            outcome_counts[s.outcome.value] += 1
            step_total_counts[s.step] += 1
            if s.outcome == StepOutcome.SUCCESS:
                step_success_counts[s.step] += 1

            n_broken = len(s.broken_invariants)
            step_broken_sums[s.step] += n_broken
            broken_by_step[s.step] = n_broken
            outcomes_by_step[s.step] = s.outcome

            # Regression check
            is_reg = s.outcome in (StepOutcome.REGRESSION, StepOutcome.BOTH) or n_broken > 0
            if is_reg and first_reg_step is None:
                first_reg_step = s.step

        seq_metrics_list.append(
            SequenceMetrics(
                sequence_id=seq_id,
                length=len(sorted_steps),
                first_regression_step=first_reg_step,
                is_censored=(first_reg_step is None),
                broken_invariants_by_step=broken_by_step,
                outcomes_by_step=outcomes_by_step,
            )
        )

    total_steps = len(records)
    outcome_rates = {
        k: (cnt / total_steps) if total_steps > 0 else 0.0
        for k, cnt in outcome_counts.items()
    }

    per_step_success = {
        step: (step_success_counts[step] / step_total_counts[step])
        for step in sorted(step_total_counts.keys())
    }

    mean_broken_by_step = {
        step: (step_broken_sums[step] / step_total_counts[step])
        for step in sorted(step_total_counts.keys())
    }

    times = [sm.first_regression_step for sm in seq_metrics_list]
    lengths = [sm.length for sm in seq_metrics_list]

    hl, is_cens = _compute_half_life(times, lengths)
    ci_low, ci_high = _bootstrap_half_life(times, lengths)

    return AggregateMetrics(
        total_sequences=len(seq_metrics_list),
        total_steps=total_steps,
        outcome_counts=outcome_counts,
        outcome_rates=outcome_rates,
        per_step_success=per_step_success,
        mean_broken_by_step=mean_broken_by_step,
        times_to_first_regression=times,
        regression_half_life=hl,
        half_life_ci_lower=ci_low,
        half_life_ci_upper=ci_high,
        is_half_life_censored=is_cens,
    )


def export_csv(metrics: AggregateMetrics, out_path: str | Path) -> None:
    """Export summary metrics and per-step series to CSV."""
    p = Path(out_path)
    p.parent.mkdir(parents=True, exist_ok=True)

    with p.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["# NOTICE", metrics.provisional_label])
        writer.writerow(["# Section: Summary Metrics"])
        writer.writerow(["metric", "value"])
        writer.writerow(["total_sequences", metrics.total_sequences])
        writer.writerow(["total_steps", metrics.total_steps])
        writer.writerow(["regression_half_life", metrics.regression_half_life])
        writer.writerow(["half_life_ci_lower", metrics.half_life_ci_lower])
        writer.writerow(["half_life_ci_upper", metrics.half_life_ci_upper])
        writer.writerow(["is_half_life_censored", metrics.is_half_life_censored])

        writer.writerow([])
        writer.writerow(["# Section: Failure Breakdown"])
        writer.writerow(["outcome", "count", "rate"])
        for o, cnt in metrics.outcome_counts.items():
            writer.writerow([o, cnt, f"{metrics.outcome_rates[o]:.4f}"])

        writer.writerow([])
        writer.writerow(["# Section: Per-Step Series"])
        writer.writerow(["step", "success_rate", "mean_broken_invariants"])
        for step in sorted(metrics.per_step_success.keys()):
            writer.writerow([
                step,
                f"{metrics.per_step_success[step]:.4f}",
                f"{metrics.mean_broken_by_step.get(step, 0.0):.4f}",
            ])


def plot_metrics(metrics: AggregateMetrics, out_png: str | Path) -> bool:
    """Generate visual plots of step curves and outcome breakdown using matplotlib."""
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        print("Warning: matplotlib not installed; skipping plot generation.", file=sys.stderr)
        return False

    p = Path(out_png)
    p.parent.mkdir(parents=True, exist_ok=True)

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(14, 5.5))
    fig.suptitle(f"Benchmark Metrics\n[{metrics.provisional_label}]", fontsize=12, fontweight="bold", y=0.98)

    # Plot 1: Cumulative broken invariants & Success rate against step
    steps = sorted(metrics.per_step_success.keys())
    if steps:
        broken_vals = [metrics.mean_broken_by_step.get(s, 0.0) for s in steps]
        success_vals = [metrics.per_step_success.get(s, 0.0) for s in steps]

        color1 = "#d9534f"
        ax1.set_xlabel("Step Index", fontsize=10)
        ax1.set_ylabel("Mean Broken Invariants", color=color1, fontsize=10)
        line1 = ax1.plot(steps, broken_vals, color=color1, marker="o", markersize=4, label="Mean Broken Invariants")
        ax1.tick_params(axis="y", labelcolor=color1)
        ax1.grid(True, linestyle="--", alpha=0.5)

        ax1_twin = ax1.twinx()
        color2 = "#337ab7"
        ax1_twin.set_ylabel("Success Rate", color=color2, fontsize=10)
        line2 = ax1_twin.plot(steps, success_vals, color=color2, marker="s", markersize=4, linestyle="--", label="Success Rate")
        ax1_twin.set_ylim(-0.05, 1.05)
        ax1_twin.tick_params(axis="y", labelcolor=color2)

        ax1.set_title("Step Policy Evolution (Mean Broken vs Success)", fontsize=11)
    else:
        ax1.text(0.5, 0.5, "No step data available", ha="center", va="center")

    # Plot 2: Outcome breakdown
    labels = list(metrics.outcome_counts.keys())
    counts = [metrics.outcome_counts[l] for l in labels]
    colors = ["#5cb85c", "#f0ad4e", "#d9534f", "#b52b27", "#777777", "#333333"]

    bars = ax2.bar(labels, counts, color=colors[:len(labels)], edgecolor="black", alpha=0.85)
    ax2.set_title("Outcome Breakdown by Class", fontsize=11)
    ax2.set_ylabel("Count", fontsize=10)
    ax2.set_xticks(range(len(labels)))
    ax2.set_xticklabels(labels, rotation=35, ha="right", fontsize=9)
    ax2.grid(axis="y", linestyle="--", alpha=0.5)

    # Annotate bar counts
    for bar in bars:
        h = bar.get_height()
        if h > 0:
            ax2.annotate(
                f"{int(h)}",
                xy=(bar.get_x() + bar.get_width() / 2, h),
                xytext=(0, 3),
                textcoords="offset points",
                ha="center",
                va="bottom",
                fontsize=9,
            )

    plt.tight_layout()
    fig.subplots_adjust(top=0.86)
    fig.savefig(str(p), dpi=150)
    plt.close(fig)
    return True


def main() -> None:
    """CLI entrypoint for metrics computation."""
    parser = argparse.ArgumentParser(
        description="PROVISIONAL: Compute benchmark regression metrics from StepResult JSONL logs."
    )
    parser.add_argument(
        "files",
        nargs="+",
        help="One or more StepResult JSONL log files to analyze.",
    )
    parser.add_argument(
        "--csv",
        default="runs/metrics_summary.csv",
        help="Destination summary CSV path (default: runs/metrics_summary.csv).",
    )
    parser.add_argument(
        "--plot",
        default="runs/metrics_plot.png",
        help="Destination plot PNG path (default: runs/metrics_plot.png).",
    )

    args = parser.parse_args()

    all_records: list[StepResult] = []
    for fpath in args.files:
        p = Path(fpath)
        if not p.is_file():
            print(f"Warning: File '{p}' not found; skipping.", file=sys.stderr)
            continue
        all_records.extend(list(read_jsonl(p, StepResult)))

    if not all_records:
        print("Error: No StepResult records loaded from provided files.", file=sys.stderr)
        sys.exit(1)

    metrics = compute_metrics(all_records)

    print("\n================================================================================")
    print("                    BENCHMARK METRICS EVALUATION SUMMARY")
    print(f"                 [{metrics.provisional_label}]")
    print("================================================================================")
    print(f"Total Sequences Evaluated : {metrics.total_sequences}")
    print(f"Total Steps Evaluated     : {metrics.total_steps}")
    print(f"Regression Half-Life      : {metrics.regression_half_life} (Censored: {metrics.is_half_life_censored})")
    print(f"Half-Life 95% Bootstrap CI: [{metrics.half_life_ci_lower}, {metrics.half_life_ci_upper}]")
    print("--------------------------------------------------------------------------------")
    print("Outcome Distribution:")
    for o, cnt in metrics.outcome_counts.items():
        pct = metrics.outcome_rates[o] * 100.0
        print(f"  {o:<14}: {cnt:4d} ({pct:5.1f}%)")
    print("================================================================================")

    export_csv(metrics, args.csv)
    print(f"Summary CSV saved to: {args.csv}")

    has_plotted = plot_metrics(metrics, args.plot)
    if has_plotted:
        print(f"Metrics plot saved to: {args.plot}")
    print()


if __name__ == "__main__":
    main()
