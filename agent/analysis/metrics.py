"""Adaptation for cuc step logs: Metric computation and visualization for policy regression benchmarks.

Ingests cuc JSONL step logs (supporting violated_now, model_errors, first_attempt_regression,
intent_type, step, condition, and model_name).
Computes:
- Per-step success / survival rate
- Cumulative broken rules / invariant violations against step across conditions (C0-C3)
- Time to first regression
- Regression half-life (censored at sequence length) with paired bootstrap 95% confidence interval (1,000 resamples)
- Failure breakdown by StepOutcome and intent_type

Outputs summary CSV and publication-ready visual plots explicitly labelled with model_name.
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


@dataclass
class SequenceMetrics:
    """Metrics calculated for an individual sequence from cuc logs."""

    sequence_id: str
    condition: str
    model_name: str
    length: int
    first_regression_step: int | None
    is_censored: bool
    broken_by_step: dict[int, int] = field(default_factory=dict)
    success_by_step: dict[int, bool] = field(default_factory=dict)


@dataclass
class AggregateMetrics:
    """Aggregated evaluation metrics across sequences."""

    total_sequences: int
    total_steps: int
    model_name: str
    condition: str
    outcome_counts: dict[str, int]
    outcome_rates: dict[str, float]
    per_step_success: dict[int, float]
    mean_broken_by_step: dict[int, float]
    regression_half_life: float | str
    half_life_ci_lower: float | str | None
    half_life_ci_upper: float | str | None
    is_half_life_censored: bool


def load_cuc_logs(file_paths: list[str | Path]) -> list[dict[str, Any]]:
    """Load and normalize JSONL step logs from cuc harness or agent runs."""
    records = []
    for fp in file_paths:
        p = Path(fp)
        if not p.is_file():
            print(f"Warning: Log file '{p}' not found; skipping.", file=sys.stderr)
            continue
        with p.open("r", encoding="utf-8") as f:
            for line in f:
                line_str = line.strip()
                if not line_str:
                    continue
                try:
                    data = json.loads(line_str)
                    # Normalize field mappings
                    if "violated_now" not in data and "broken_invariants" in data:
                        data["violated_now"] = len(data["broken_invariants"])
                    if "model_name" not in data:
                        data["model_name"] = data.get("model", "unknown_model")
                    if "condition" not in data:
                        data["condition"] = "C0"
                    if "outcome" not in data:
                        # Infer outcome if missing
                        if data.get("parse_ok", True) and data.get("new_intent_ok", True) and data.get("violated_now", 0) == 0:
                            data["outcome"] = "SUCCESS"
                        else:
                            data["outcome"] = "REGRESSION" if data.get("violated_now", 0) > 0 else "PARSE_FAIL"
                    records.append(data)
                except (json.JSONDecodeError, ValueError):
                    continue
    return records


def _compute_half_life(times: list[int | None], lengths: list[int]) -> tuple[float | str, bool]:
    """Calculate median time to first regression treating non-regressed runs as censored."""
    if not times:
        return 0.0, False

    effective_times = [
        t if t is not None else length for t, length in zip(times, lengths)
    ]
    sorted_times = sorted(effective_times)
    n = len(sorted_times)
    mid = n // 2

    regressed_count = sum(1 for t in times if t is not None)
    if regressed_count < (n / 2.0):
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
    """Compute paired bootstrap 95% confidence interval for regression half-life (1,000+ resamples)."""
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


def compute_metrics(records: list[dict[str, Any]]) -> AggregateMetrics:
    """Compute aggregate benchmark metrics from normalized cuc records."""
    seq_records: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in records:
        seq_id = r.get("sequence_id", "unknown_seq")
        seq_records[seq_id].append(r)

    seq_metrics_list: list[SequenceMetrics] = []
    outcome_counts: dict[str, int] = defaultdict(int)
    step_success_counts: dict[int, int] = defaultdict(int)
    step_total_counts: dict[int, int] = defaultdict(int)
    step_broken_sums: dict[int, int] = defaultdict(int)

    model_names = set()
    conditions = set()

    for seq_id, steps in seq_records.items():
        sorted_steps = sorted(steps, key=lambda s: s.get("step", 0))
        first_reg_step: int | None = None
        broken_by_step: dict[int, int] = {}
        success_by_step: dict[int, bool] = {}

        seq_model = "unknown"
        seq_cond = "C0"

        for s in sorted_steps:
            seq_model = s.get("model_name", seq_model)
            seq_cond = s.get("condition", seq_cond)
            model_names.add(seq_model)
            conditions.add(seq_cond)

            outcome = str(s.get("outcome", "UNKNOWN"))
            outcome_counts[outcome] += 1

            step_idx = int(s.get("step", 0))
            step_total_counts[step_idx] += 1

            is_success = s.get("parse_ok", True) and s.get("new_intent_ok", True) and int(s.get("violated_now", 0)) == 0
            if is_success:
                step_success_counts[step_idx] += 1
            success_by_step[step_idx] = is_success

            v_now = int(s.get("violated_now", 0))
            step_broken_sums[step_idx] += v_now
            broken_by_step[step_idx] = v_now

            is_reg = v_now > 0 or s.get("first_attempt_regression", False) or outcome in ("REGRESSION", "BOTH")
            if is_reg and first_reg_step is None:
                first_reg_step = step_idx

        seq_metrics_list.append(
            SequenceMetrics(
                sequence_id=seq_id,
                condition=seq_cond,
                model_name=seq_model,
                length=len(sorted_steps),
                first_regression_step=first_reg_step,
                is_censored=(first_reg_step is None),
                broken_by_step=broken_by_step,
                success_by_step=success_by_step,
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
    ci_low, ci_high = _bootstrap_half_life(times, lengths, n_resamples=1000)

    primary_model = ", ".join(model_names) if model_names else "unknown_model"
    primary_cond = ", ".join(conditions) if conditions else "C0"

    return AggregateMetrics(
        total_sequences=len(seq_metrics_list),
        total_steps=total_steps,
        model_name=primary_model,
        condition=primary_cond,
        outcome_counts=dict(outcome_counts),
        outcome_rates=outcome_rates,
        per_step_success=per_step_success,
        mean_broken_by_step=mean_broken_by_step,
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
        writer.writerow(["# Model", metrics.model_name])
        writer.writerow(["# Condition", metrics.condition])
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
        writer.writerow(["step", "success_rate", "mean_broken_rules"])
        for step in sorted(metrics.per_step_success.keys()):
            writer.writerow([
                step,
                f"{metrics.per_step_success[step]:.4f}",
                f"{metrics.mean_broken_by_step.get(step, 0.0):.4f}",
            ])


def plot_metrics(metrics: AggregateMetrics, out_png: str | Path) -> bool:
    """Generate publication-ready visual plots explicitly labelled with model_name."""
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
    fig.suptitle(f"Benchmark Metrics — Model: {metrics.model_name} (Condition: {metrics.condition})", fontsize=12, fontweight="bold", y=0.98)

    steps = sorted(metrics.per_step_success.keys())
    if steps:
        broken_vals = [metrics.mean_broken_by_step.get(s, 0.0) for s in steps]
        success_vals = [metrics.per_step_success.get(s, 0.0) for s in steps]

        color1 = "#d9534f"
        ax1.set_xlabel("Step Index", fontsize=10)
        ax1.set_ylabel("Mean Broken Rules (violated_now)", color=color1, fontsize=10)
        ax1.plot(steps, broken_vals, color=color1, marker="o", markersize=4, label="Mean Broken Rules")
        ax1.tick_params(axis="y", labelcolor=color1)
        ax1.grid(True, linestyle="--", alpha=0.5)

        ax1_twin = ax1.twinx()
        color2 = "#337ab7"
        ax1_twin.set_ylabel("Success Rate", color=color2, fontsize=10)
        ax1_twin.plot(steps, success_vals, color=color2, marker="s", markersize=4, linestyle="--", label="Success Rate")
        ax1_twin.set_ylim(-0.05, 1.05)
        ax1_twin.tick_params(axis="y", labelcolor=color2)

        ax1.set_title(f"[{metrics.model_name}] Step Policy Evolution", fontsize=11)
    else:
        ax1.text(0.5, 0.5, "No step data available", ha="center", va="center")

    labels = list(metrics.outcome_counts.keys())
    counts = [metrics.outcome_counts[l] for l in labels]
    colors = ["#5cb85c", "#f0ad4e", "#d9534f", "#b52b27", "#777777", "#333333"]

    bars = ax2.bar(labels, counts, color=colors[:len(labels)], edgecolor="black", alpha=0.85)
    ax2.set_title(f"[{metrics.model_name}] Outcome Breakdown", fontsize=11)
    ax2.set_ylabel("Count", fontsize=10)
    ax2.set_xticks(range(len(labels)))
    ax2.set_xticklabels(labels, rotation=35, ha="right", fontsize=9)
    ax2.grid(axis="y", linestyle="--", alpha=0.5)

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
    """CLI entrypoint for metrics analysis."""
    parser = argparse.ArgumentParser(
        description="Compute benchmark regression metrics and bootstrap CI from cuc JSONL logs."
    )
    parser.add_argument(
        "files",
        nargs="+",
        help="One or more cuc JSONL log files to analyze.",
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

    records = load_cuc_logs(args.files)
    if not records:
        print("Error: No valid records loaded from provided files.", file=sys.stderr)
        sys.exit(1)

    metrics = compute_metrics(records)

    print("\n================================================================================")
    print(f"            BENCHMARK METRICS EVALUATION — MODEL: {metrics.model_name}")
    print(f"            CONDITION: {metrics.condition}")
    print("================================================================================")
    print(f"Total Sequences Evaluated : {metrics.total_sequences}")
    print(f"Total Steps Evaluated     : {metrics.total_steps}")
    print(f"Regression Half-Life      : {metrics.regression_half_life} (Censored: {metrics.is_half_life_censored})")
    print(f"Half-Life 95% Bootstrap CI: [{metrics.half_life_ci_lower}, {metrics.half_life_ci_upper}] (1,000 resamples)")
    print("--------------------------------------------------------------------------------")
    print("Outcome Distribution:")
    for o, cnt in metrics.outcome_counts.items():
        pct = metrics.outcome_rates[o] * 100.0
        print(f"  {o:<14}: {cnt:4d} ({pct:5.1f}%)")
    print("================================================================================")

    export_csv(metrics, args.csv)
    print(f"Summary CSV saved to: {args.csv}")

    plotted = plot_metrics(metrics, args.plot)
    if plotted:
        print(f"Metrics plot saved to: {args.plot}")
    print()


if __name__ == "__main__":
    main()
