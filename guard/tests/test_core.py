import os, sys, unittest
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from cuc.harness import run_sequence
from cuc.intents import generate_sequence
from cuc.invariants import ReferenceBackend, baseline_invariants
from cuc.iosmodel import ParseIssue, parse_config
from cuc.models import CorruptedGoldModel, GoldModel, SimModel
from cuc.oracle import build_cases
from cuc.testbed import STANDARD_BEDS, get_testbed, legacy_testbed
from cuc.topology import build_baseline

B = ReferenceBackend()


class Core(unittest.TestCase):
    def test_baseline_parses_and_all_invariants_hold(self):
        res = B.check(build_baseline(), baseline_invariants())
        self.assertTrue(all(r.passed for r in res.values()))

    def test_oracle_30_of_30(self):
        base, invs = build_baseline(), baseline_invariants()
        cases = build_cases()
        self.assertEqual(len(cases), 30)
        for c in cases:
            got = {i for i, r in B.check(c.edit(base), invs).items() if not r.passed}
            self.assertEqual(got, c.expected_broken, c.id)

    def test_parser_is_strict(self):
        t = build_baseline()["E1"]
        for bad in (t.replace("permit tcp", "permitt tcp", 1), t + "\nbanner motd hello\n",
                    t.replace("ip access-group TO_E1 out", "ip access-group NOPE out")):
            with self.assertRaises(ParseIssue):
                parse_config(bad, "E1")

    def test_sequences_are_reproducible_and_gold_clean(self):
        a, ma = generate_sequence(3, 25, B)
        b, mb = generate_sequence(3, 25, B)
        self.assertEqual(a, b)
        recs = run_sequence(a, GoldModel(), "C0", B, "t")
        self.assertTrue(all(r["outcome"] == "SUCCESS" and r["violated_now"] == 0 for r in recs))

    def test_corrupted_steps_all_flagged(self):
        its, _ = generate_sequence(5, 20, B)
        corrupt = [2, 5, 8, 11, 14, 17]
        recs = run_sequence(its, CorruptedGoldModel(corrupt), "C0", B, "t")
        self.assertTrue(all(r["outcome"] != "SUCCESS" for r in recs if r["step"] in corrupt))

    def test_c3_never_commits_a_regression(self):
        its, _ = generate_sequence(9, 40, B)
        m = SimModel("sim-bad", 0.8, 0.6, 0.1)
        recs = run_sequence(its, m, "C3", B, "t")
        self.assertTrue(all(not (r["broken_invariants"] and r["committed"]) for r in recs))
        self.assertEqual(recs[-1]["violated_now"], 0)
        c0 = run_sequence(its, m, "C0", B, "t")
        self.assertGreater(c0[-1]["violated_now"], 0)       # and the unguarded run really does drift

    def test_same_first_attempt_across_conditions(self):
        its, _ = generate_sequence(11, 15, B)
        m = SimModel("sim-x", 0.4, 0.3, 0.05)
        c0 = run_sequence(its, m, "C0", B, "a")[0]
        c1 = run_sequence(its, m, "C1", B, "b")[0]
        c3 = run_sequence(its, m, "C3", B, "c")[0]
        # step 1 starts from the same state, so the first proposal and its verdict are identical
        self.assertEqual(c0["first_attempt_bad"], c1["first_attempt_bad"])
        self.assertEqual(c0["first_attempt_bad"], c3["first_attempt_bad"])


if __name__ == "__main__":
    unittest.main()


class Zoo(unittest.TestCase):
    def test_standard_beds_baseline_holds(self):
        for name in STANDARD_BEDS:
            tb = get_testbed(name)
            res = B.check(tb.baseline, tb.invariants)
            self.assertTrue(all(r.passed for r in res.values()), name)
            self.assertEqual(len(tb.invariants), 20)
            self.assertTrue(any(i.kind == "waypoint" for i in tb.invariants))

    def test_testbed_is_deterministic(self):
        self.assertEqual(get_testbed("abilene").baseline, get_testbed("abilene").baseline)

    def test_gold_sequence_clean_on_zoo_bed(self):
        tb = get_testbed("abilene")
        its, _ = generate_sequence(5, 15, B, testbed=tb)
        recs = run_sequence(its, GoldModel(), "C0", B, "t", testbed=tb)
        self.assertTrue(all(r["outcome"] == "SUCCESS" and r["violated_now"] == 0 for r in recs))

    def test_legacy_sequence_unchanged_by_refactor(self):
        a, _ = generate_sequence(3, 10, B)
        b, _ = generate_sequence(3, 10, B, testbed=legacy_testbed())
        self.assertEqual([x["params"] for x in a], [x["params"] for x in b])


class PrefixFilter(unittest.TestCase):
    def setUp(self):
        from cuc.intents import apply_intent, make_invariant
        self.apply, self.mk = apply_intent, make_invariant
        self.tb = get_testbed("abilene")
        reach = [i for i in self.tb.invariants if i.kind == "reach"]
        key = {c: k for k, c in self.tb.subnets.items()}
        mk0 = lambda src, dst: dict(type="prefix_filter", source=src, destination=dst, protocol="ip", port=0,
                                    device=self.tb.host_dev[key[src]], plist="X", action="filter")
        self.r = self.free_dst = None
        for r in reach:                                      # a source with (a) a reachable-by-invariant pair and (b) a
            reach_dsts = {i.destination for i in reach if i.source == r.source}       # destination reachable w/o invariant
            for c in self.tb.subnets.values():
                if c != r.source and c not in reach_dsts and not B.check(self.tb.baseline, [self.mk(1, mk0(r.source, c))])["N001"].passed:
                    self.r, self.free_dst = r, c
                    break
            if self.r:
                break
        assert self.r, "no suitable pair on this bed"
        self.src = key[self.r.source]
        self.dev = self.tb.host_dev[self.src]
        self.mkp = lambda dst: dict(type="prefix_filter", source=self.r.source, destination=dst, protocol="ip", port=0,
                                    device=self.dev, plist=f"RF_{self.dev}", action="filter")

    def holds(self, texts, inv):
        return B.check(texts, [inv])[inv.id].passed

    def broken(self, texts):
        return {i for i, r in B.check(texts, self.tb.invariants).items() if not r.passed}

    def test_gold_blocks_target_and_breaks_nothing_else(self):
        p = self.mkp(self.free_dst)
        new = self.apply(self.tb.baseline, p)
        self.assertTrue(self.holds(new, self.mk(1, p)))
        self.assertEqual(self.broken(new), set())

    def test_conflicting_filter_breaks_existing_reach(self):
        p = self.mkp(self.r.destination)                     # kills a reachability invariant: generator must discard
        self.assertIn(self.r.id, self.broken(self.apply(self.tb.baseline, p)))

    def test_variants_misbehave_as_designed(self):
        p = self.mkp(self.free_dst)
        base = self.tb.baseline
        broad, impl = self.apply(base, p, broad=True), self.apply(base, p, misplaced=True)
        self.assertIn("deny 10.1.0.0/16 le 32", broad[self.dev])
        self.assertNotIn("permit 0.0.0.0/0", impl[self.dev])
        self.assertIn(self.r.id, self.broken(broad))         # over-broad filter kills the reach invariant
        self.assertIn(self.r.id, self.broken(impl))          # implicit deny drops every route
        with self.assertRaises(ParseIssue):
            parse_config(self.apply(base, p, syntax_error=True)[self.dev], self.dev)

    def test_prefix_list_matching_and_strictness(self):
        import ipaddress
        from cuc.iosmodel import parse_prefix_line
        _, l = parse_prefix_line("X seq 5 deny 10.1.0.0/16 le 24".split())
        self.assertTrue(l.matches(ipaddress.ip_network("10.1.3.0/24")))
        self.assertFalse(l.matches(ipaddress.ip_network("10.2.3.0/24")))
        _, e = parse_prefix_line("X seq 5 deny 10.1.3.0/24".split())
        self.assertFalse(e.matches(ipaddress.ip_network("10.1.3.0/25")))      # exact length only
        for bad in ("X seq 5 deny 10.1.3.0/24 le 20", "X seq five deny 10.1.3.0/24", "X seq 5 allow 10.1.3.0/24"):
            with self.assertRaises(ParseIssue):
                parse_prefix_line(bad.split())
        t = self.apply(self.tb.baseline, self.mkp(self.free_dst))[self.dev].replace(
            f"distribute-list prefix RF_{self.dev} in", "distribute-list prefix NOPE in")
        with self.assertRaises(ParseIssue):
            parse_config(t, self.dev)

    def test_second_filter_and_shadowing(self):
        other = next(c for c in self.tb.subnets.values() if c not in (self.r.source, self.free_dst, self.r.destination))
        base = self.apply(self.tb.baseline, self.mkp(other))                  # first filter creates the list
        p2 = self.mkp(self.r.destination)                                     # now filter a reachable destination
        inv2 = self.mk(2, p2)
        self.assertTrue(self.holds(self.apply(base, p2), inv2))
        self.assertFalse(self.holds(self.apply(base, p2, misplaced=True), inv2))   # deny after permit-any is never reached

    def test_dependency_footprint_sees_prefix_list_edit(self):
        from cuc.edits import diff_footprint
        fp = diff_footprint(self.tb.baseline, self.apply(self.tb.baseline, self.mkp(self.free_dst)))
        self.assertIn((self.dev, "prefixlist", f"RF_{self.dev}"), fp)
        self.assertIn((self.dev, "ospf", "*"), fp)

    def test_generated_prefix_filter_sequence_is_gold_clean(self):
        its, _ = generate_sequence(21, 3, B, testbed=self.tb, intent_types=("prefix_filter",))
        self.assertTrue(all(i["type"] == "prefix_filter" for i in its))
        recs = run_sequence(its, GoldModel(), "C0", B, "t", testbed=self.tb)
        self.assertTrue(all(r["outcome"] == "SUCCESS" and r["violated_now"] == 0 for r in recs))


class Waypoint(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._setup(cls)

    def setUp(self):
        pass

    def _setup(self):
        from cuc.intents import generate_sequence as gs
        self.tb = get_testbed("bteurope")
        its, _ = gs(31, 60, B, testbed=self.tb, intent_types=("acl", "prefix_filter", "waypoint"))
        self.wp = [i for i in its if i["type"] == "waypoint"]
        # rebuild the state just before the first waypoint step
        state = dict(self.tb.baseline)
        for it in its:
            if it is self.wp[0]:
                break
            state.update(it["gold_patch"])
        self.state, self.it = state, self.wp[0]

    def test_generator_produces_waypoint_steps(self):
        self.assertTrue(self.wp)

    def test_gold_steers_every_path_and_variants_fail(self):
        from cuc.intents import apply_intent
        from cuc.invariants import Invariant
        inv = Invariant(**self.it["invariant"])
        self.assertFalse(B.check(self.state, [inv])[inv.id].passed)          # not true before the edit
        self.assertTrue(B.check(apply_intent(self.state, self.it["params"]), [inv])[inv.id].passed)
        self.assertFalse(B.check(apply_intent(self.state, self.it["params"], misplaced=True), [inv])[inv.id].passed)
        with self.assertRaises(ParseIssue):
            bad = apply_intent(self.state, self.it["params"], syntax_error=True)
            for d in bad:
                parse_config(bad[d], d)

    def test_ospf_cost_parsing_is_strict(self):
        t = self.tb.baseline["R1"].replace(" no shutdown", " ip ospf cost 0\n no shutdown", 1)
        with self.assertRaises(ParseIssue):
            parse_config(t, "R1")

    def test_cost_edit_shows_in_footprint(self):
        from cuc.edits import diff_footprint
        fp = diff_footprint(self.state, {**self.state, **self.it["gold_patch"]})
        self.assertTrue(all(k == "iface" for _, k, _ in fp))
        self.assertEqual({d for d, _, _ in fp}, set(self.it["gold_patch"]))



class Vrf(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from cuc.intents import apply_intent, make_invariant
        cls.apply, cls.mk = staticmethod(apply_intent), staticmethod(make_invariant)
        cls.tb = get_testbed("abilene")
        g = cls.tb.guests[1]
        cls.p = dict(type="vrf_isolate", source=g, destination=cls.tb.subnets[2], protocol="ip", port=0,
                     device=cls.tb.host_dev[1], vrf="GUEST", action="isolate")

    def test_guest_reachable_before_and_isolated_after(self):
        inv = self.mk(1, self.p)
        self.assertFalse(B.check(self.tb.baseline, [inv])[inv.id].passed)
        new = self.apply(self.tb.baseline, self.p)
        self.assertTrue(B.check(new, [inv])[inv.id].passed)
        self.assertTrue(all(r.passed for r in B.check(new, self.tb.invariants).values()))

    def test_leak_and_over_isolation(self):
        inv = self.mk(1, self.p)
        leak = self.apply(self.tb.baseline, self.p, misplaced=True)
        self.assertFalse(B.check(leak, [inv])[inv.id].passed)                 # guests still get through
        over = self.apply(self.tb.baseline, self.p, broad=True)
        self.assertTrue(B.check(over, [inv])[inv.id].passed)
        hosts = self.tb.subnets[1]
        reach_from_site = [i for i in self.tb.invariants if i.kind == "reach" and hosts in (i.source, i.destination)]
        if reach_from_site:
            res = B.check(over, reach_from_site)
            self.assertTrue(any(not r.passed for r in res.values()))      # the whole site went dark

    def test_vrf_order_and_reference_are_strict(self):
        t = self.apply(self.tb.baseline, self.p)[self.p["device"]]
        swapped = t.replace(" ip vrf forwarding GUEST\n ip address 10.2.1.1 255.255.255.0",
                            " ip address 10.2.1.1 255.255.255.0\n ip vrf forwarding GUEST")
        self.assertNotEqual(swapped, t)
        with self.assertRaises(ParseIssue):
            parse_config(swapped, self.p["device"])
        with self.assertRaises(ParseIssue):
            parse_config(t.replace("ip vrf GUEST\n", ""), self.p["device"])          # undefined VRF
        with self.assertRaises(ParseIssue):
            parse_config(t.replace("ip vrf GUEST\n", "ip vrf GUEST\n rd 65000:1\n"), self.p["device"])   # Batfish rejects rd



class OutputParsing(unittest.TestCase):
    def test_unclosed_and_language_labelled_blocks(self):
        from cuc.harness import parse_output
        cfg = "hostname E1\n!\ninterface GigabitEthernet0/0\n no shutdown\n!"
        self.assertEqual(set(parse_output("```E1\n" + cfg)), {"E1"})                      # no closing fence
        self.assertEqual(set(parse_output("Sure:\n```cisco\n" + cfg + "\n```\nDone.")), {"E1"})
        two = "```E1\n" + cfg + "\n```\n```R2\n" + cfg.replace("E1", "R2")              # second one unclosed
        self.assertEqual(set(parse_output(two)), {"E1", "R2"})
        with self.assertRaises(ParseIssue):
            parse_output("I cannot help with that.")


    def test_vrf_definition_form(self):
        tb = get_testbed("abilene")
        dev = tb.host_dev[1]
        t = tb.baseline[dev].replace(" description guests\n ip address", " description guests\n vrf forwarding G\n ip address")
        t += "!\nvrf definition G\n rd 65000:1\n !\n address-family ipv4\n exit-address-family\n!\n"
        cfg = parse_config(t, dev)
        self.assertIn("G", cfg.vrfs)
        g = {v.ip.network.with_prefixlen: v.vrf for v in cfg.interfaces.values() if v.ip}
        self.assertEqual(g[tb.guests[1]], "G")
        from cuc.invariants import Invariant
        inv = Invariant("X", "isolate", tb.guests[1], tb.subnets[2], {"protocol": "ip"})
        self.assertTrue(B.check({**tb.baseline, dev: t}, [inv])["X"].passed)   # isolated, same as 'ip vrf'


class Resume(unittest.TestCase):
    def test_resumed_run_equals_uninterrupted_run(self):
        import hashlib, json
        from cuc.models import fence
        tb = get_testbed("abilene")
        its, _ = generate_sequence(100, 8, B, testbed=tb, seq_id="t")
        by_text = {it["text"]: it for it in its}

        class FakeLLM:
            name, last_usage = "fake", None

            def complete(self, prompt):
                req = prompt.split("CHANGE REQUEST: ")[1].split("\n")[0]
                if int(hashlib.sha256(prompt.encode()).hexdigest(), 16) % 3 == 0:
                    return "not sure"
                return fence(by_text[req]["gold_patch"])
        strip = lambda rs: [{k: v for k, v in r.items() if k != "latency_s"} for r in rs]
        for cond in ("C0", "C3"):
            full = run_sequence(its, FakeLLM(), cond, B, "x", testbed=tb)
            res = run_sequence(its, FakeLLM(), cond, B, "x", testbed=tb, resume=json.loads(json.dumps(full[:4])))
            self.assertEqual(strip(res), strip(full))
        bad = json.loads(json.dumps(full[:3]))
        bad[2]["violated_now"] += 5
        with self.assertRaises(ValueError):
            run_sequence(its, FakeLLM(), "C3", B, "x", testbed=tb, resume=bad)
