"""Summarize model runs written by run_models.py (Phase 4 / 7).

    python3 scripts/summarize_runs.py RUNS_DIR [--out results/real_runs]

RUNS_DIR is the --out folder of run_models.py.  Writes a markdown table (one row per model x bed x condition)
and paired-bootstrap comparisons between conditions on the same sequences, plus a JSON with everything.
Token counts are measured when the backend reports usage (tokens_measured), else estimated (~4 chars/token)."""
import argparse, glob, json, os, sys
from collections import defaultdict
ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, ROOT)
from cuc.metrics import paired_bootstrap, per_sequence, summarize

ap = argparse.ArgumentParser()
ap.add_argument("runs_dir")
ap.add_argument("--out", default=os.path.join(ROOT, "results", "real_runs"))
a = ap.parse_args()

groups = defaultdict(dict)                     # (model, bed, cond) -> {seq: [recs]}
for f in glob.glob(os.path.join(a.runs_dir, "runs", "*", "*", "*", "*.jsonl")):
    model, bed, cond = f.split(os.sep)[-4:-1]
    recs = [json.loads(l) for l in open(f)]
    groups[(model, bed, cond)][recs[0]["sequence_id"]] = sorted(recs, key=lambda r: r["step"])
if not groups:
    sys.exit(f"no runs under {a.runs_dir}/runs")
os.makedirs(a.out, exist_ok=True)
rows, per = [], {}
for (model, bed, cond), runs in sorted(groups.items()):
    n = max(len(r) for r in runs.values())
    s = summarize(runs, n)
    measured = all(r.get("tokens_measured") for run in runs.values() for r in run)
    errs = sum(r.get("model_errors", 0) for run in runs.values() for r in run)
    if errs:
        print(f"WARNING: {model}/{bed}/{cond} has {errs} runtime model errors (e.g. GPU out of memory): rerun those sequences")
    rows.append(dict(model=model, bed=bed, condition=cond, steps_per_seq=n, tokens_measured=measured, runtime_errors=errs, **s))
    per[(model, bed, cond)] = per_sequence(runs, n)

fmt = lambda x: "-" if x is None else (f"{x:.1%}" if isinstance(x, float) and x <= 1 else (f"{x:.2f}" if isinstance(x, float) else str(x)))
md = ["| model | bed | cond | seqs | half-life (steps) | runs w/ regression | broken at end | intent done | rejected | parse fail | retries/step | tokens in/step |",
      "|---|---|---|---|---|---|---|---|---|---|---|---|"]
for r in rows:
    hl = f"over {r['steps_per_seq']}" if r["half_life_censored"] else f"{r['half_life']:.0f}"
    md.append(f"| {r['model']} | {r['bed']} | {r['condition']} | {r['sequences']} | {hl} | {r['runs_with_regression']}/{r['sequences']} | "
              f"{r['final_violated_mean']:.2f} | {fmt(r['completion_rate'])} | {fmt(r['rejected_rate'])} | {fmt(r['parse_fail_rate'])} | "
              f"{r['mean_retries']:.2f} | {r['tokens_in_per_step']:.0f}{'' if r['tokens_measured'] else ' (est.)'} |")
md += ["", "Paired bootstrap over sequences (difference = second condition minus first; 5000 resamples):", "",
       "| model | bed | comparison | metric | mean diff | 95% CI | p |", "|---|---|---|---|---|---|---|"]
comps = []
for (model, bed) in sorted({(m, b) for m, b, _ in per}):
    for c1, c2 in (("C0", "C1"), ("C1", "C2"), ("C1", "C3"), ("C0", "C3")):
        A, Bv = per.get((model, bed, c1)), per.get((model, bed, c2))
        if not A or not Bv:
            continue
        for metric in ("final_violated", "first_regression_step", "completion", "tokens_in"):
            m, (lo, hi), p = paired_bootstrap({k: v[metric] for k, v in A.items()}, {k: v[metric] for k, v in Bv.items()})
            if m is None:
                continue
            comps.append(dict(model=model, bed=bed, a=c1, b=c2, metric=metric, mean_diff=m, ci=(lo, hi), p=p, n=len(set(A) & set(Bv))))
            md.append(f"| {model} | {bed} | {c1} vs {c2} | {metric} | {m:+.3f} | [{lo:+.3f}, {hi:+.3f}] | {p:.3f} |")
open(os.path.join(a.out, "summary.md"), "w").write("\n".join(md) + "\n")
json.dump(dict(rows=rows, comparisons=comps), open(os.path.join(a.out, "summary.json"), "w"), indent=1, default=str)
print("\n".join(md))
