"""Evaluation harness (Phase 3) with conditions C0 / C1 / C2 / C3 (Phase 6 guard).

C0  open loop      : candidate is committed even if it breaks things (drift accumulates)
C1  reactive       : on failure, counterexample feedback and up to 3 retries; the last candidate is
                     committed regardless (so residual breakage persists)
C3  full guard     : retries as in C1, but a candidate is committed ONLY if it parses, breaks no
                     protected invariant and achieves its intent; otherwise rollback -> REJECTED.
                     The prompt carries the verified-state ledger instead of chat history.
C2  dependency-aware: as C1, but the prompt also lists the tracked invariants whose dependencies touch the
     devices being edited (from the dependency map).  Only meaningful for a real LLM - a simulated model ignores
     the prompt, so for SimModel C2 behaves exactly like C1.

A real model implements complete(prompt) -> text and optionally last_usage = (tokens_in, tokens_out); the
harness then sends it exactly the prompt below.  Simulated models implement propose(...) instead.
"""
from __future__ import annotations

import json
import os
import re
import time
from typing import Dict, List, Optional

from .edits import diff_footprint
from .engine import diff_flows, load_network
from .invariants import Invariant
from .testbed import Testbed, legacy_testbed
from .iosmodel import ParseIssue
from .topology import write_snapshot

Texts = Dict[str, str]
FENCE = re.compile(r"```[ \t]*([^\n`]*)\n(.*?)```", re.S)
SYSTEM = ("You are a network configuration editor for Cisco IOS routers. Apply the requested change and nothing "
          "else. Return the FULL new configuration of every device you change, one fenced code block per device, "
          "with the device name as the block label (for example ```R1). Do not change devices that do not need "
          "to change. No explanations.\n\n" + """ALLOWED CONFIGURATION STATEMENTS (a Cisco IOS subset; any other statement is rejected):
- hostname NAME
- interface NAME, with: description TEXT | ip address A.B.C.D MASK | ip access-group ACL in|out | ip ospf cost N |
  ip vrf forwarding VRF (or vrf forwarding VRF), placed before ip address | shutdown | no shutdown
- ip access-list extended NAME, with lines: SEQ permit|deny ip|tcp|udp|icmp SRC DST [eq PORT | range P1 P2]
  where SRC and DST are: any | host A.B.C.D | A.B.C.D WILDCARD
- ip prefix-list NAME seq N permit|deny A.B.C.D/LEN [ge X] [le Y]
- router ospf 1, with: network A.B.C.D WILDCARD area 0 | distribute-list prefix NAME in
- ip vrf VRF   or   vrf definition VRF (optionally with: rd X:Y, address-family ipv4, exit-address-family)
Routing is OSPF only: change paths with ip ospf cost, filter routes with a prefix-list + distribute-list, and
separate networks with VRFs. There are no static routes, route-maps, numbered access-lists or other protocols.""")
_HOST = re.compile(r"^hostname (\S+)", re.M)


def parse_output(text: str) -> Texts:
    """Fenced blocks -> {device: config}.  The label is the device name; if a model labels a block with a
    language ('cisco', 'text') instead, the device is taken from the block's 'hostname' line."""
    text = text or ""
    if text.count("```") % 2 == 1:
        # models often stop right after the last config line without writing the closing fence
        # (seen with Qwen2.5-Coder-7B on Colab): close it, so a complete answer is not a parse failure
        text = text.rstrip() + "\n```"
    blocks = FENCE.findall(text)
    if not blocks:
        raise ParseIssue("no fenced device blocks in model output")
    out: Texts = {}
    for label, body in blocks:
        m = _HOST.search(body)
        dev = m.group(1) if m else label.strip()
        if not dev:
            raise ParseIssue("a fenced block has neither a device label nor a hostname line")
        if dev in out:
            raise ParseIssue(f"device {dev} returned twice")
        body = body.rstrip() + "\n"
        out[dev] = body
    return out


def apply_output(state: Texts, outs: Texts) -> Texts:
    for dev in outs:
        if dev not in state:
            raise ParseIssue(f"model returned unknown device '{dev}'")
    new = dict(state)
    new.update(outs)
    return new


def build_ledger(tracked: Dict[str, Invariant], last_res) -> str:
    rows = [f"{i}: {tracked[i].describe()} -- {'HOLDS' if last_res[i].passed else 'BROKEN'}" for i in tracked]
    return "VERIFIED STATE LEDGER\n" + "\n".join(rows)


def intent_devices(intent: dict) -> List[str]:
    p = intent["params"]
    return list(p.get("devices") or [p["device"]])


def build_prompt(state: Texts, intent: dict, condition: str, ledger: str = "", feedback: Optional[str] = None,
                 affected: Optional[List[str]] = None) -> str:
    parts = [SYSTEM] + [f"--- {d} ---\n{state[d]}" for d in intent_devices(intent)]
    if condition == "C3" and ledger:
        parts.append(ledger)
    if condition == "C2" and affected:
        parts.append("POLICIES THAT DEPEND ON THESE DEVICES (they must keep holding after your change)\n" + "\n".join(affected))
    parts.append(f"CHANGE REQUEST: {intent['text']}")
    if feedback:
        parts.append("YOUR PREVIOUS ATTEMPT FAILED:\n" + feedback)
    return "\n\n".join(parts)


def _overlaps(deps, footprint) -> bool:
    for dev, kind, name, _ in deps:
        for fdev, fkind, fname in footprint:
            if dev != fdev:
                continue
            if fkind == "other":
                return True
            if kind == fkind and (name == fname or "*" in (name, fname)):
                return True
            if kind == "route" and fkind in ("ospf", "iface", "prefixlist"):
                return True
            if kind == "prefixlist" and fkind in ("ospf", "iface"):
                return True
            if fkind == "iface" and kind == "acl":
                return True
    return False


def _feedback(parse_err, newly, res, tracked, inv_new, ok_new) -> str:
    out = []
    if parse_err:
        out.append(f"- the configuration did not parse: {parse_err}")
    for i in newly:
        f = res[i].example_flow
        ex = f" Example flow: {f.src_ip} -> {f.dst_ip} {f.proto}/{f.dport}." if f else ""
        out.append(f"- you broke {i} ({tracked[i].describe()}).{ex} {res[i].detail}")
    if not parse_err and not ok_new:
        out.append(f"- the requested change was not achieved: {inv_new.describe()} ({res[inv_new.id].detail})")
    return "\n".join(out)


def est_tokens(s: str) -> int:
    return max(1, len(s) // 4)            # rough estimate (~4 chars/token), labelled as such in the report


def run_sequence(intents: List[dict], model, condition: str, backend, run_id: str, mode: str = "open",
                 max_retries: int = 3, snapshot_root: Optional[str] = None,
                 testbed: Optional[Testbed] = None, on_step=None, resume: Optional[List[dict]] = None) -> List[dict]:
    """on_step(rec) is called after every step (progress reporting / partial logs).
    resume = records of the first k steps of an interrupted run of THIS sequence/model/condition (real models only:
    they carry raw_outputs).  The state after step k is rebuilt from them without calling the model, then the run
    continues at step k+1.  Raises ValueError if the rebuilt state does not match what was logged."""
    assert condition in ("C0", "C1", "C2", "C3") and mode in ("open", "oracle_reset")
    tb = testbed or legacy_testbed()
    state = dict(tb.baseline)
    gold = dict(state)
    tracked: Dict[str, Invariant] = {i.id: i for i in tb.invariants}
    last_res = backend.check(state, list(tracked.values()))
    holding = {i for i, r in last_res.items() if r.passed}
    broken_set = set(tracked) - holding
    recs: List[dict] = []
    for it, old in zip(intents, resume or []):
        inv_new = Invariant(**it["invariant"])
        if old.get("step") != it["step"] or "raw_outputs" not in old:
            raise ValueError("resume records do not belong to this sequence or carry no model outputs")
        if old["committed"]:
            cand = apply_output(state, parse_output(old["raw_outputs"][-1]))
            res = backend.check(cand, list(tracked.values()) + [inv_new])
            state = cand
            if old["new_intent_ok"]:
                tracked[inv_new.id] = inv_new
            last_res = res
        if mode == "oracle_reset":
            gold = {**gold, **it["gold_patch"]}
            state = dict(gold)
            tracked[inv_new.id] = inv_new
            last_res = backend.check(state, list(tracked.values()))
        holding = {i for i in tracked if last_res[i].passed}
        broken_set = set(tracked) - holding
        if len(broken_set) != old["violated_now"]:
            raise ValueError(f"rebuilt state disagrees with the log at step {it['step']}")
        recs.append(old)
    for it in intents[len(recs):]:
        inv_new = Invariant(**it["invariant"])
        attempts, feedback = 0, None
        tin = tout = 0
        t0 = time.time()
        first_attempt_bad = None
        devs = set(intent_devices(it))
        affected_desc = [f"{i}: {tracked[i].describe()}" for i in tracked
                         if i in holding and any(d[0] in devs for d in last_res[i].deps)] if condition == "C2" else None
        raw_outputs = []
        model_errors = 0
        while True:
            ledger = build_ledger(tracked, last_res) if condition == "C3" else ""
            prompt = build_prompt(state, it, condition, ledger, feedback, affected_desc)
            usage = None
            try:
                if hasattr(model, "complete"):
                    raw = model.complete(prompt)
                    usage = getattr(model, "last_usage", None)
                else:
                    raw = model.propose(state, it, feedback, attempts)
            except Exception as e:
                raw = ""
                raw_outputs.append(f"<model error: {type(e).__name__}: {e}>")
                model_errors += 1
            if hasattr(model, "complete"):
                raw_outputs.append(raw)
            if usage:
                tin, tout = tin + usage[0], tout + usage[1]
            else:
                tin, tout = tin + est_tokens(prompt), tout + est_tokens(raw)
            parse_err, cand, res, newly, ok_new = None, None, None, [], False
            try:
                cand = apply_output(state, parse_output(raw))
                res = backend.check(cand, list(tracked.values()) + [inv_new])
                newly = [i for i in tracked if i in holding and not res[i].passed]
                ok_new = res[inv_new.id].passed
            except ParseIssue as e:
                parse_err = str(e)
            bad = bool(parse_err) or bool(newly) or not ok_new
            if first_attempt_bad is None:
                first_attempt_bad = dict(regression=bool(newly), bad=bad)
            if condition == "C0" or not bad or attempts >= max_retries:
                break
            feedback = _feedback(parse_err, newly, res, tracked, inv_new, ok_new)
            attempts += 1

        parse_ok = parse_err is None
        if parse_err:
            attempt_outcome = "PARSE_FAIL"
        elif newly and not ok_new:
            attempt_outcome = "BOTH"
        elif newly:
            attempt_outcome = "REGRESSION"
        elif not ok_new:
            attempt_outcome = "FORWARD_FAIL"
        else:
            attempt_outcome = "SUCCESS"

        if condition == "C3":                       # C0, C1 and C2 commit whatever parsed
            commit = attempt_outcome == "SUCCESS"
            outcome = "SUCCESS" if commit else "REJECTED"
        else:
            commit = parse_ok
            outcome = attempt_outcome

        dep_miss, affected_n, flow_changes = 0, 0, []
        if parse_ok:
            fp = diff_footprint(state, cand)
            affected = {i for i in tracked if _overlaps(last_res[i].deps, fp)}
            affected_n = len(affected)
            dep_miss = sum(1 for i in newly if i not in affected)
            if newly and hasattr(backend, "name") and backend.name == "reference":
                flow_changes = diff_flows(load_network(state), load_network(cand), tb.subnet_list())[:5]

        old_broken = set(broken_set)
        if commit:
            state = cand
            if ok_new:
                tracked[inv_new.id] = inv_new
            last_res = res
        if mode == "oracle_reset":
            gold = {**gold, **it["gold_patch"]}
            state = dict(gold)
            tracked[inv_new.id] = inv_new
            last_res = backend.check(state, list(tracked.values()))
        holding = {i for i in tracked if last_res[i].passed}
        broken_set = set(tracked) - holding
        rec = dict(
            run_id=run_id, model=model.name, condition=condition, mode=mode, sequence_id=it["sequence_id"],
            step=it["step"], parse_ok=parse_ok, new_intent_ok=ok_new, broken_invariants=newly,
            outcome=outcome, attempt_outcome=attempt_outcome, retries=attempts, tokens_in=tin, tokens_out=tout,
            latency_s=round(time.time() - t0, 4), committed=bool(commit),
            violated_now=len(broken_set), repaired=sorted(old_broken - broken_set),
            trap=it["trap"], trap_kind=it["trap_kind"], intent_type=it["type"],
            first_attempt_regression=first_attempt_bad["regression"], first_attempt_bad=first_attempt_bad["bad"],
            dep_map_miss=dep_miss, affected_checked=affected_n, tracked_n=len(tracked), flow_changes=flow_changes,
        )
        rec["model_errors"] = model_errors      # runtime errors (e.g. GPU out of memory), NOT model mistakes
        if hasattr(model, "complete"):
            rec["raw_outputs"] = raw_outputs
            rec["tokens_measured"] = usage is not None
        recs.append(rec)
        if on_step is not None:
            on_step(rec)
        if snapshot_root:
            write_snapshot(state, os.path.join(snapshot_root, run_id, f"step_{it['step']}"))
    return recs


def write_jsonl(path: str, recs: List[dict], append: bool = False) -> None:
    with open(path, "a" if append else "w") as f:
        for r in recs:
            f.write(json.dumps(r, sort_keys=True) + "\n")
