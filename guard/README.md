# Coherence Under Change: checker, guard and real-model runs (Track A)

Measures how quickly an LLM breaks earlier network policy while applying a long sequence of config edits, and
prevents it with a pre-commit guard. This folder is Track A: test beds, the checker (cross-checked against
Batfish), the harness with conditions C0 to C3, the guard and its dependency map, the real-model runner and the
Kaggle/Colab notebooks.

## Result so far (Qwen2.5-Coder-7B-Instruct, 4-bit, Abilene, 3 sequences x 15 steps per condition)
| | C0 open loop | C1 feedback + retries | C2 + affected policies in prompt | C3 full guard |
|---|---|---|---|---|
| edits that succeeded | 25 of 45 | 31 of 45 | 29 of 45 | 29 of 45 |
| committed regressions | 5 | 4 | 2 | 0 |
| policies broken at step 15 (per sequence) | 4, 1, 3 | 4, 3, 1 | 0, 3, 2 | 0, 0, 0 |
| prompt tokens per step | 920 | 2,176 | 2,959 | 4,435 |

Second model, same 3 sequences (Llama-3.1-8B-Instruct, 4-bit, C0 and C3 only):
| | C0 open loop | C3 full guard |
|---|---|---|
| edits that succeeded | 16 of 45 | 8 of 45 |
| committed regressions | 5 | 0 |
| policies broken at step 15 (per sequence) | 4, 2, 7 | 0, 0, 0 |
| edits rejected by the guard | none | 37 (82%) |
| prompt tokens per step | 837 | 5,765 |

The guard prevents drift for both models; how much work still gets through depends on the model (Llama's C3
answers were mostly invalid configs under the long ledger prompt).

Logs and summaries: `results_real/` (`summary/summary.md` for Qwen, `summary_llama/summary.md` for Llama). Three
sequences are a pilot: lead with the counts, the bootstrap intervals are coarse.

## Layout
| path | what it is |
|---|---|
| `cuc/` | the package: IOS-subset parser (`iosmodel.py`), reference engine (`engine.py`), invariants + dependency map (`invariants.py`), Batfish backend, test beds, intent generator, harness and guard (`harness.py`), model wrappers (`llm.py`) |
| `scripts/` | validation against Batfish, model runs, summaries, figures, fine-tuning data |
| `tests/` | 28 unit tests |
| `notebooks/` | `kaggle_runs.ipynb` (Qwen, all conditions), `kaggle_llama.ipynb` (Llama-3.1-8B, C0 + C3), `colab_runs.ipynb` |
| `data/` | Topology Zoo maps used for the test beds (Abilene, BtEurope, Cesnet201006) |
| `sequences/` | frozen 9-device test sequences, seeds 100-119 (byte-reproducible) |
| `results/` | simulated-model results and figures (October 15 prototype) |
| `results_real/` | real-model step logs (one JSON line per step, model answers included) and summary |

## What is real and what is simulated
- **Judge.** `cuc/engine.py` is a Python reference engine for the Cisco-IOS subset used here (OSPF with interface
  costs and ECMP, interface ACLs, OSPF inbound prefix-list filters, VRFs). `cuc/batfish_backend.py` runs the same
  checks on real Batfish. Model runs use the reference engine, cross-checked against Batfish by the
  `scripts/validate_*` scripts (all agree; see below).
- **Models.** `results_real/` is a real model. `results/` comes from `SimModel`, a seeded simulated LLM with set
  error rates: it shows the pipeline, not real LLM behaviour.

## Test beds (`cuc/testbed.py`)
| bed | source | devices | invariants | role |
|---|---|---|---|---|
| legacy9 | hand-built | 9 | 15 | prototype, frozen sequences |
| abilene | Topology Zoo `Abilene.gml`, 11 core routers | 17 | 20 | training |
| bteurope | Topology Zoo `BtEurope.gml`, 24 core routers | 32 | 20 | training |
| cesnet | Topology Zoo `Cesnet201006.gml`, 52 core routers | 62 | 20 | held-out test |

## Intent types (`cuc/intents.py`)
| type | gold patch | trap variants |
|---|---|---|
| acl_permit / acl_deny | add an ACL line (deny before the catch-all permit) | `prefix_overlap`, `shadowing` |
| prefix_filter | inbound OSPF `distribute-list prefix` + trailing permit-all | `prefix_overlap`, `implicit_deny`, `shadowing` |
| waypoint | raise OSPF costs (both ends of a link) until every equal-cost path crosses router W | `cost_wrong_end`, `over_steering` |
| vrf_isolate | move a guest network into VRF GUEST | `isolation_leak`, `over_isolation` |

## Conditions (`cuc/harness.py`)
C0 open loop · C1 counterexample feedback + 3 retries · C2 = C1 + the prompt lists the policies whose dependencies
touch the devices being edited · C3 full guard: commit only if it parses, breaks nothing tracked and achieves the
intent, else roll back (REJECTED); the prompt carries the verified-state ledger.
Note: the team branch (`feat-jhithesh-agent-framework`) defines C1-C3 differently; the team still has to pick one.

## Validation against Batfish (`docker run -d --name batfish -p 8888:8888 -p 9997:9997 -p 9996:9996 batfish/allinone`)
    python3 scripts/validate_against_batfish.py           # 30 of 30: human, checker and Batfish (9-device bed)
    python3 scripts/validate_zoo_batfish.py 4             # 39 of 39 on the three Zoo beds
    python3 scripts/validate_zoo_batfish.py 3 wp vrf      # 30 of 30, waypoint and VRF intents
    python3 scripts/validate_zoo_mutations.py 3           # 93 of 93 breaking edits
    python3 scripts/validate_depmap_batfish.py            # dependency map: 0 misses over 106 broken invariants
    python3 scripts/batfish_parse_audit.py results_real   # parser vs Batfish on the model's real answers

## Model runs
Long runs go on Kaggle (free T4 GPUs, about 30 hours a week): open `notebooks/kaggle_runs.ipynb` (instructions in
its first cell) and use **Save Version → Save & Run All**. Interactive sessions stop after about 40 idle minutes
and lose their files; saved versions keep running with the tab closed. Every finished step is saved and an
interrupted run resumes from the last one.

    python3 scripts/run_models.py --backend hf --model Qwen/Qwen2.5-Coder-7B-Instruct --name qwen2.5-coder-7b \
        --bed abilene --seeds 100-102 --length 15 --conds C0,C3,C1,C2 --workers 3 --out RUNS_DIR
    python3 scripts/summarize_runs.py RUNS_DIR             # table + paired bootstrap between conditions
    python3 scripts/make_sft_data.py                       # QLoRA data from training beds + leakage check

## Other scripts
    python3 -m unittest discover -s tests                  # 28 tests
    python3 scripts/run_oracle.py                          # 30 of 30 oracle cases
    N_SEQ=20 LENGTH=100 python3 scripts/run_experiment.py  # simulated models (TESTBED=abilene|bteurope|cesnet)
    python3 scripts/measure_depmap.py 5 60                 # dependency-map misses and savings, simulated model
    python3 scripts/build_testbeds.py                      # write each bed's configs + invariants

Requirements: Python 3.9+ (the core uses only the standard library); `matplotlib` for figures; `pybatfish` and
Docker for the Batfish scripts; `transformers`,
`accelerate`, `bitsandbytes` (and `peft` for fine-tuning) on a GPU for model runs.
