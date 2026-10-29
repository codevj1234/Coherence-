"""Fine-tuning data for Phase 5 (QLoRA) and the leakage check.

Training examples come ONLY from the training beds (default: abilene, bteurope) and training seeds (1000+), never
from the held-out test bed (cesnet) or the test seeds (100-119).  Each example is one step: the prompt the harness
would send (C1 style, plus a C3-style copy with the ledger for half the steps) and the gold patch as the answer.

    python3 scripts/make_sft_data.py [--train-beds abilene,bteurope] [--n-seq 40] [--length 50] [--out data/sft]

Leakage check (printed and saved): no test-bed device names in training answers, no training seed among the test
seeds, and no training prompt identical to any prompt of the test sequences (test sequences on the training beds
included).  Exits non-zero on any leak."""
import argparse, hashlib, json, os, random, sys
ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, ROOT)
from cuc.harness import build_ledger, build_prompt
from cuc.intents import generate_sequence
from cuc.invariants import Invariant, ReferenceBackend
from cuc.models import fence
from cuc.testbed import STANDARD_BEDS, get_testbed

ap = argparse.ArgumentParser()
ap.add_argument("--train-beds", default="abilene,bteurope")
ap.add_argument("--test-bed", default="cesnet")
ap.add_argument("--n-seq", type=int, default=40)
ap.add_argument("--length", type=int, default=50)
ap.add_argument("--test-seeds", default="100-119")
ap.add_argument("--test-length", type=int, default=30)
ap.add_argument("--out", default=os.path.join(ROOT, "data", "sft"))
a = ap.parse_args()
lo, hi = map(int, a.test_seeds.split("-"))
test_seeds = set(range(lo, hi + 1))
train_seeds = list(range(1000, 1000 + a.n_seq))
B = ReferenceBackend()
rng = random.Random(7)
os.makedirs(a.out, exist_ok=True)
h = lambda s: hashlib.sha256(s.encode()).hexdigest()


def prompts_for(tb, its, with_ledger):
    state = dict(tb.baseline)
    tracked = {i.id: i for i in tb.invariants}
    for it in its:
        res = B.check(state, list(tracked.values()))
        ledger = build_ledger(tracked, res) if with_ledger(it) else ""
        yield it, state, build_prompt(state, it, "C3" if ledger else "C1", ledger)
        state = {**state, **it["gold_patch"]}
        inv = Invariant(**it["invariant"])
        tracked[inv.id] = inv


# prompts of every test sequence on every bed: training examples identical to one of them are removed
test_hashes = set()
for bed in list(STANDARD_BEDS):
    tb = get_testbed(bed)
    for s in sorted(test_seeds):
        its, _ = generate_sequence(s, a.test_length, B, testbed=tb, seq_id=f"{bed}-s{s}")
        for _, _, p in prompts_for(tb, its, lambda it: False):
            test_hashes.add(h(p))

examples, train_hashes, removed = [], set(), 0
for bed in a.train_beds.split(","):
    assert bed != a.test_bed, "a training bed cannot be the test bed"
    tb = get_testbed(bed)
    for s in train_seeds:
        its, _ = generate_sequence(s, a.length, B, testbed=tb, seq_id=f"train-{bed}-s{s}")
        for it, state, prompt in prompts_for(tb, its, lambda it: rng.random() < 0.5):
            if h(prompt) in test_hashes:
                removed += 1
                continue
            answer = fence(it["gold_patch"])
            examples.append({"messages": [{"role": "user", "content": prompt}, {"role": "assistant", "content": answer}],
                             "meta": {"bed": bed, "seed": s, "step": it["step"], "type": it["type"], "trap": it["trap"]}})
            train_hashes.add(h(prompt))
rng.shuffle(examples)
n_val = max(1, len(examples) // 20)
with open(os.path.join(a.out, "train.jsonl"), "w") as f:
    for e in examples[n_val:]:
        f.write(json.dumps(e) + "\n")
with open(os.path.join(a.out, "val.jsonl"), "w") as f:
    for e in examples[:n_val]:
        f.write(json.dumps(e) + "\n")

# ---- leakage check ----
leaks = []
if set(train_seeds) & test_seeds:
    leaks.append(f"training seeds overlap test seeds: {sorted(set(train_seeds) & test_seeds)}")
test_dev = set(get_testbed(a.test_bed).baseline)
train_dev = set().union(*[set(get_testbed(b).baseline) for b in a.train_beds.split(",")])
test_only = test_dev - train_dev                     # device names that exist only on the test bed
bad = sum(1 for e in examples if any(f"```{d}\n" in e["messages"][1]["content"] for d in test_only))
if bad:
    leaks.append(f"{bad} training answers name devices that exist only on the test bed")
overlap = len(test_hashes & train_hashes)
if overlap:
    leaks.append(f"{overlap} test prompts appear verbatim in the training data")
report = dict(removed_identical_to_test=removed, train_examples=len(examples) - n_val, val_examples=n_val, train_beds=a.train_beds, test_bed=a.test_bed,
              train_seeds=f"{train_seeds[0]}-{train_seeds[-1]}", test_seeds=a.test_seeds,
              by_type={t: sum(e["meta"]["type"] == t for e in examples) for t in sorted({e["meta"]["type"] for e in examples})},
              leaks=leaks)
json.dump(report, open(os.path.join(a.out, "leakage_report.json"), "w"), indent=1)
print(json.dumps(report, indent=1))
sys.exit(1 if leaks else 0)
