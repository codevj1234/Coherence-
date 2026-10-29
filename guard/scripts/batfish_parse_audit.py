"""Does the project's strict config parser reject exactly what Batfish rejects?  Checked on REAL model answers.

For every device config a model returned (taken from run logs), compare
  ours    - cuc.iosmodel.parse_config accepts / rejects it (and why)
  batfish - Batfish's fileParseStatus for that file is PASSED / not (and its parse warnings)
All parse failures are checked, plus up to --controls answers our parser accepted.  Needs the Batfish container.

    python3 scripts/batfish_parse_audit.py RUNS_DIR [--controls 15]

RUNS_DIR = a copy of the Drive folder cuc_runs (it contains runs/<model>/<bed>/<condition>/*.jsonl).
Paste the output back: each DIFF line is a config where the two disagree."""
import argparse, glob, json, os, random, shutil, sys, tempfile
ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, ROOT)
from cuc.harness import parse_output
from cuc.iosmodel import ParseIssue, parse_config
from cuc.testbed import get_testbed

ap = argparse.ArgumentParser()
ap.add_argument("runs_dir")
ap.add_argument("--controls", type=int, default=15)
a = ap.parse_args()

answers = []                                   # (bed, label, device, config text, our verdict, our reason)
for f in sorted(glob.glob(os.path.join(a.runs_dir, "runs", "*", "*", "*", "*.jsonl"))):
    model, bed, cond = f.split(os.sep)[-4:-1]
    for line in open(f):
        r = json.loads(line)
        for k, raw in enumerate(r.get("raw_outputs") or []):
            try:
                outs = parse_output(raw)
            except ParseIssue:
                continue                       # no config at all: nothing for Batfish to parse
            for dev, text in outs.items():
                try:
                    parse_config(text, dev)
                    ok, why = True, ""
                except ParseIssue as e:
                    ok, why = False, str(e)
                label = f"{model}/{bed}/{cond}/{r['sequence_id']} step {r['step']} try {k}"
                answers.append((bed, label, dev, text, ok, why))
bad = [x for x in answers if not x[4]]
good = [x for x in answers if x[4]]
random.Random(0).shuffle(good)
todo = bad + good[:a.controls]
print(f"{len(answers)} device configs in the logs: {len(bad)} rejected by our parser, checking all of them "
      f"plus {min(len(good), a.controls)} accepted ones as controls", flush=True)

from pybatfish.client.session import Session
bf = Session(host="localhost")
bf.set_network("cuc_parse_audit")
agree = 0
outside = 0                                   # rejected by us, accepted by Batfish: valid IOS outside the prompt's dialect
beds = {}
for bed, label, dev, text, ok, why in todo:
    tb = beds.setdefault(bed, get_testbed(bed))
    if dev not in tb.baseline:
        print(f"  skip {label}: unknown device {dev}")
        continue
    d = tempfile.mkdtemp()
    os.makedirs(os.path.join(d, "configs"))
    for dv, t in tb.baseline.items():
        open(os.path.join(d, "configs", f"{dv}.cfg"), "w").write(text if dv == dev else t)
    bf.init_snapshot(d, name="a", overwrite=True)
    shutil.rmtree(d, ignore_errors=True)
    st = bf.q.fileParseStatus().answer().frame()
    st = st[st["File_Name"].astype(str).str.endswith(f"/{dev}.cfg")]
    status = str(st["Status"].iloc[0]) if len(st) else "?"
    warn = ""
    if status != "PASSED":
        try:
            w = bf.q.parseWarning().answer().frame()
            if len(w):
                w = w[w["Filename"].astype(str).str.endswith(f"/{dev}.cfg")]
                warn = "; ".join(f"line {r.Line}: {str(r.Text)[:60]}" for r in w.itertuples())[:200]
        except Exception as e:
            warn = f"(parseWarning failed: {e})"
    same = ok == (status == "PASSED")
    agree += same
    outside += (not ok) and status == "PASSED"
    print(f"  {'OK  ' if same else 'DIFF'} {label} {dev}: ours={'accept' if ok else 'REJECT'}"
          f"{'' if ok else ' (' + why[:90] + ')'} | batfish={status}{' ' + warn if warn else ''}", flush=True)
print(f"\nParser vs Batfish on real model answers: {agree}/{len(todo)} agree")
print(f"Rejected by our parser but accepted by Batfish (valid IOS outside the statements the prompt allows): {outside}")
print("Since the prompt lists the allowed statements, those count as instruction violations; report them separately.")
