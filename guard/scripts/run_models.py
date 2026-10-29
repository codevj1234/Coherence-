"""Run real (or simulated) models through the harness - resumable, for Colab (Phase 4).

Every finished (model, bed, condition, sequence) run is written to its own JSONL file under --out; a rerun skips
files that are already complete, so a Colab disconnect costs at most the sequences that were in flight.
Put --out on Google Drive.

  # local vLLM server (see the notebook), 4 sequences in parallel:
  python3 scripts/run_models.py --backend openai --base-url http://localhost:8000/v1 \\
      --model Qwen/Qwen2.5-Coder-7B-Instruct-AWQ --name qwen7b --bed abilene --seeds 100-104 --length 30 \\
      --conds C0,C1,C2,C3 --workers 4 --out /content/drive/MyDrive/cuc_runs
  # plain transformers (slower, one sequence at a time):
  python3 scripts/run_models.py --backend hf --model Qwen/Qwen2.5-Coder-7B-Instruct --name qwen7b-hf ...
  # pipeline check without a GPU:
  python3 scripts/run_models.py --backend gold --bed abilene --seeds 100-101 --length 10 --conds C0,C3 --out /tmp/x
"""
import argparse, json, os, sys, threading, time
from concurrent.futures import ThreadPoolExecutor, as_completed
ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, ROOT)
from cuc.harness import run_sequence
from cuc.intents import generate_sequence, read_sequence, write_sequence
from cuc.invariants import ReferenceBackend
from cuc.testbed import get_testbed


def seeds_arg(s):
    if "-" in s:
        a, b = s.split("-")
        return list(range(int(a), int(b) + 1))
    return [int(x) for x in s.split(",")]


ap = argparse.ArgumentParser()
ap.add_argument("--backend", choices=["openai", "hf", "gold", "sim"], required=True)
ap.add_argument("--model", default="")
ap.add_argument("--name", default="")
ap.add_argument("--base-url", default="http://localhost:8000/v1")
ap.add_argument("--api-key-env", default="", help="name of the env var holding an API key (hosted endpoints)")
ap.add_argument("--min-interval", type=float, default=0.0, help="seconds between requests (free hosted tiers)")
ap.add_argument("--bed", default="abilene")
ap.add_argument("--seeds", type=seeds_arg, default=seeds_arg("100-104"))
ap.add_argument("--length", type=int, default=30)
ap.add_argument("--conds", default="C0,C1,C2,C3")
ap.add_argument("--workers", type=int, default=1)
ap.add_argument("--max-tokens", type=int, default=2048)
ap.add_argument("--adapter", default="", help="LoRA adapter folder for --backend hf (fine-tuned model)")
ap.add_argument("--out", required=True)
a = ap.parse_args()

B = ReferenceBackend()
tb = get_testbed(a.bed)
conds = a.conds.split(",")


def make_model():
    if a.backend == "openai":
        from cuc.llm import OpenAICompatModel
        key = os.environ.get(a.api_key_env, "EMPTY") if a.api_key_env else "EMPTY"
        return OpenAICompatModel(a.base_url, a.model, key, name=a.name or None, max_tokens=a.max_tokens,
                                 min_interval_s=a.min_interval)
    if a.backend == "hf":
        from cuc.llm import HFChatModel
        return HFChatModel(a.model, name=a.name or None, max_new_tokens=a.max_tokens, adapter=a.adapter or None,
                           max_batch=max(1, a.workers))
    if a.backend == "gold":
        from cuc.models import GoldModel
        return GoldModel()
    from cuc.models import SimModel
    return SimModel("sim-small", p_broad=0.45, p_misplace=0.35, p_syntax=0.05)


# frozen test sequences: generated once, then always read back (identical across models)
seq_dir = os.path.join(a.out, "sequences")
os.makedirs(seq_dir, exist_ok=True)
seqs = []
for s in a.seeds:
    path = os.path.join(seq_dir, f"{a.bed}_seed{s}_len{a.length}.jsonl")
    if not os.path.exists(path):
        its, meta = generate_sequence(s, a.length, B, testbed=tb, seq_id=f"{a.bed}-s{s}")
        write_sequence(path, its, meta)
    seqs.append(read_sequence(path)[0])

shared = make_model() if a.backend == "hf" else None       # one copy of the weights
name = (a.name or getattr(shared or make_model(), "name", a.backend))
run_dir = os.path.join(a.out, "runs", name, a.bed)
todo = []
for cond in conds:
    os.makedirs(os.path.join(run_dir, cond), exist_ok=True)
    for its in seqs:
        f = os.path.join(run_dir, cond, f"{its[0]['sequence_id']}.jsonl")
        if os.path.exists(f) and sum(1 for _ in open(f)) == len(its):
            continue
        todo.append((cond, its, f))
print(f"{name} on {a.bed}: {len(todo)} runs to do ({len(conds)} conditions x {len(seqs)} sequences x {a.length} steps); "
      f"{len(conds) * len(seqs) - len(todo)} already done", flush=True)
local = threading.local()
lock = threading.Lock()
t0 = time.time()
done = 0


def work(job):
    cond, its, f = job
    if shared is not None:
        model = shared
    else:
        if not hasattr(local, "m"):
            local.m = make_model()
        model = local.m
    t = time.time()
    partial = f + ".partial"
    resume = []
    if os.path.exists(partial):                            # continue an interrupted sequence from its step log
        for line in open(partial):
            try:
                r = json.loads(line)
            except ValueError:
                break                                      # a half-written last line
            if r.get("model_errors") or "raw_outputs" not in r:
                break
            resume.append(r)
    with open(partial, "w") as fh:
        for r in resume:
            fh.write(json.dumps(r, sort_keys=True) + "\n")
    if resume:
        with lock:
            print(f"    {cond} {its[0]['sequence_id']}: resuming after step {len(resume)} (rebuilt from the step log)", flush=True)

    def on_step(r):
        with open(partial, "a") as fh:                     # progress you can watch on Drive
            fh.write(json.dumps(r, sort_keys=True) + "\n")
        with lock:
            print(f"    {cond} {its[0]['sequence_id']} step {r['step']}/{len(its)}: {r['outcome']}"
                  f" (retries {r['retries']}, {r['latency_s']:.0f}s)", flush=True)

    rid = f"{name}-{a.bed}-{cond}-{its[0]['sequence_id']}"
    try:
        recs = run_sequence(its, model, cond, B, rid, testbed=tb, on_step=on_step, resume=resume)
    except ValueError as e:                                # log unusable: start this sequence over
        with lock:
            print(f"    {cond} {its[0]['sequence_id']}: cannot resume ({e}); starting from step 1", flush=True)
        open(partial, "w").close()
        recs = run_sequence(its, model, cond, B, rid, testbed=tb, on_step=on_step)
    if any(r.get("model_errors") for r in recs):
        os.replace(partial, f + ".errors") if os.path.exists(partial) else None
        return cond, its[0]["sequence_id"] + " (HAD RUNTIME ERRORS - not saved, rerun the cell)", time.time() - t, recs
    tmp = f + ".tmp"
    with open(tmp, "w") as fh:
        for r in recs:
            fh.write(json.dumps(r, sort_keys=True) + "\n")
    os.replace(tmp, f)
    if os.path.exists(partial):
        os.remove(partial)
    return cond, its[0]["sequence_id"], time.time() - t, recs


workers = max(1, a.workers)          # hf: requests of parallel sequences are batched on the GPU
with ThreadPoolExecutor(max_workers=workers) as ex:
    for fut in as_completed([ex.submit(work, j) for j in todo]):
        cond, sid, dt, recs = fut.result()
        with lock:
            done += 1
            el = time.time() - t0
            eta = el / done * (len(todo) - done)
            ok = sum(r["outcome"] == "SUCCESS" for r in recs)
            reg = sum(bool(r["broken_invariants"]) and r["committed"] for r in recs)
            print(f"[{done}/{len(todo)}] {cond} {sid}: {ok}/{len(recs)} steps succeeded, {reg} committed regressions, "
                  f"{dt/60:.1f} min (elapsed {el/60:.0f} min, ETA {eta/60:.0f} min)", flush=True)
print("all requested runs are complete")
