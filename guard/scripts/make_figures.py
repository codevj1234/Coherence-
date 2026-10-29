"""Figures + results tables from results/steps.jsonl (simulated models -- see README)."""
import json, os, sys
ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, ROOT)
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from cuc import metrics as M

N = int(os.environ.get("LENGTH", 100))
recs = M.load(f"{ROOT}/results/steps.jsonl")
g = M.group_runs(recs)
COL = {"C0": "#2a78d6", "C1": "#eb6834", "C3": "#1baf7a"}
LAB = {"C0": "C0 open loop", "C1": "C1 reactive", "C3": "C3 full guard"}
INK, MUTED, GRID = "#0b0b0b", "#52514e", "#e4e3df"
plt.rcParams.update({"font.size": 10, "axes.edgecolor": GRID, "axes.labelcolor": MUTED,
                     "xtick.color": MUTED, "ytick.color": MUTED, "axes.spines.top": False,
                     "axes.spines.right": False, "axes.grid": True, "grid.color": GRID, "grid.linewidth": 0.6})
models = ["sim-small", "sim-large"]


def panel_fig(fn, ylabel, title, draw, ylim=None):
    fig, axs = plt.subplots(1, 2, figsize=(11, 4), sharey=True)
    for ax, m in zip(axs, models):
        for c in ("C0", "C1", "C3"):
            draw(ax, g[(m, c, "open")], c)
        ax.set_title(f"{m} (simulated)", loc="left", color=INK, fontsize=11)
        ax.set_xlabel("step in change sequence")
        if ylim: ax.set_ylim(*ylim)
    axs[0].set_ylabel(ylabel)
    axs[0].legend(handles=[plt.Line2D([], [], color=COL[c], lw=2, label=LAB[c]) for c in COL], frameon=False, loc="upper left")
    fig.suptitle(title, x=0.01, ha="left", color=INK, fontsize=12)
    fig.tight_layout()
    fig.savefig(f"{ROOT}/results/{fn}", dpi=160)
    plt.close(fig)


def draw_decay(ax, runs, c):
    ys = M.decay_curve(runs, N)
    ax.plot(range(1, N + 1), ys, color=COL[c], lw=2)


def draw_surv(ax, runs, c):
    firsts = [M.first_regression_step(r, N) for r in runs.values()]
    ys = [sum(f > k for f in firsts) / len(firsts) for k in range(0, N + 1)]
    ax.step(range(0, N + 1), ys, where="post", color=COL[c], lw=2)
    ax.axhline(0.5, color=MUTED, lw=0.8, ls=":")


panel_fig("fig1_decay_curves.png", "mean broken invariants (of those verified earlier)",
          "Drift: invariants silently broken as the sequence grows (20 sequences x 100 steps)", draw_decay)
panel_fig("fig2_regression_survival.png", "share of runs with no committed regression yet",
          "Regression half-life: where each curve crosses 0.5 (dotted)", draw_surv, ylim=(0, 1.05))

# Failure breakdown by trap kind (first attempt, C0 open loop)
fig, ax = plt.subplots(figsize=(7.5, 3.8))
kinds = ["None", "prefix_overlap", "shadowing+prefix_overlap"]
names = ["non-trap steps", "over-broad rule\n(prefix overlap)", "over-broad or\nmisplaced rule"]
w = 0.36
for j, (m, col) in enumerate(zip(models, ["#2a78d6", "#eb6834"])):
    b = M.breakdown(g[(m, "C0", "open")], "trap_kind")
    xs = [i + (j - 0.5) * w for i in range(3)]
    ax.bar(xs, [b[k][1] * 100 for k in kinds], w - 0.04, color=col, label=f"{m} (simulated)")
ax.set_xticks(range(3)); ax.set_xticklabels(names)
ax.set_ylabel("steps that regress on first try (%)")
ax.set_title("Trap labels predict where edits break earlier policy", loc="left", color=INK, fontsize=11)
ax.legend(frameon=False)
fig.tight_layout(); fig.savefig(f"{ROOT}/results/fig3_trap_breakdown.png", dpi=160); plt.close(fig)

# Ablation table
rows = []
for m in models:
    for c, mode in [("C0", "open"), ("C0", "oracle_reset"), ("C1", "open"), ("C3", "open")]:
        s = M.summarize(g[(m, c, mode)], N)
        hl = f">{N}" if s["half_life_censored"] else f"{s['half_life']:.0f} (95% CI {s['half_life_ci'][0]:.0f}-{s['half_life_ci'][1]:.0f})"
        label = LAB[c] + (" + oracle reset" if mode == "oracle_reset" else "")
        rows.append((m, label, hl, f"{s['runs_with_regression']}/20", f"{s['regression_step_rate']*100:.1f}%",
                     f"{s['final_violated_mean']:.2f}", f"{s['completion_rate']*100:.1f}%", f"{s['rejected_rate']*100:.1f}%",
                     f"{s['mean_retries']:.2f}", f"{s['tokens_in_per_step']:.0f}"))
hdr = ("model", "condition", "half-life (steps)", "runs with regression", "regressing steps",
       "broken invariants at step 100", "intent completed", "rejected", "retries/step", "tokens in/step (est.)")
md = "| " + " | ".join(hdr) + " |\n|" + "---|" * len(hdr) + "\n" + "\n".join("| " + " | ".join(r) + " |" for r in rows)
open(f"{ROOT}/results/ablation_table.md", "w").write(md + "\n")
print(md)
