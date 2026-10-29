"""Offline tests of BatfishOracle's verdict and diagnosis logic, with a fake pybatfish session.

They check how traces are turned into verdicts and explanations (the part this branch owns); agreement with real
Batfish is checked by `python -m generator.oracle.validate_batfish` against a running container.
"""

from types import SimpleNamespace as NS

import pytest

from agent.contract.models import Invariant, InvariantKind
from generator.oracle.batfish import BatfishNotAvailableError, BatfishOracle

CONFIGS = {
    "r1": "hostname r1\n!\ninterface Gi0/0\n ip address 10.0.1.1 255.255.255.0\n ip access-group IN in\n!\n"
          "interface Gi0/1\n ip address 10.9.0.1 255.255.255.252\n!\n"
          "ip access-list extended IN\n deny tcp any any eq 22\n permit ip any any\n!\n",
    "fw": "hostname fw\n!\ninterface Gi0/0\n ip address 10.9.0.2 255.255.255.252\n!\n"
          "interface Gi0/1\n ip address 10.0.2.1 255.255.255.0\n!\n",
}


class Frame(list):
    """Just enough of a pandas frame: rows, a column, itertuples, iloc."""

    def __getitem__(self, key):
        if isinstance(key, str):
            return [row[key] for row in self]
        return list.__getitem__(self, key)

    def itertuples(self):
        return [NS(**row) for row in self]

    @property
    def iloc(self):
        return self


def trace(disposition, nodes, denied=None):
    hops = []
    for n in nodes:
        steps = [NS(action="DENIED", detail=NS(filter="IN"))] if n == denied else [NS(action="FORWARDED", detail=None)]
        hops.append(NS(node=n, steps=steps))
    return NS(disposition=disposition, hops=hops)


class FakeSession:
    def __init__(self, traces, line="deny tcp any any eq 22"):
        self.traces, self.line, self.calls = traces, line, []
        self.q = NS(
            traceroute=lambda **kw: self._ans(kw, Frame([{"Traces": self.traces}])),
            fileParseStatus=lambda: NS(answer=lambda: NS(frame=lambda: Frame(
                [{"File_Name": "configs/r1.cfg", "Status": "PASSED"}]))),
            testFilters=lambda **kw: NS(answer=lambda: NS(frame=lambda: Frame([{"Line_Content": self.line}]))),
        )

    def _ans(self, kw, frame):
        self.calls.append(kw)
        return NS(answer=lambda: NS(frame=lambda: frame))

    def init_snapshot(self, *a, **kw):
        pass


def oracle_with(traces, **kw):
    o = BatfishOracle.__new__(BatfishOracle)
    o.host, o.snapshot_dir, o.network = "x", None, "t"
    o._available, o._hc = True, (lambda **h: h)
    o._client = FakeSession(traces, **kw)
    o._snap_key, o._models, o.last_parse_status, o.last_unlocated = None, {}, {}, []
    from generator.oracle.reference import ReferenceOracle
    o._ref = ReferenceOracle()
    return o


def inv(kind, src="10.0.1.10", dst="10.0.2.10", headers=None, via=None):
    return Invariant(id="i", kind=kind, source=src, destination=dst, headers=headers or {"protocol": "tcp", "dst_port": 80},
                     via=via, created_at_step=0)


def test_unavailable_raises():
    o = oracle_with([])
    o._available = False
    with pytest.raises(BatfishNotAvailableError):
        o.check(CONFIGS, [inv(InvariantKind.REACH)])


def test_reach_isolate_waypoint_verdicts():
    ok = [trace("DELIVERED_TO_SUBNET", ["r1", "fw"])]
    assert oracle_with(ok).check(CONFIGS, [inv(InvariantKind.REACH)]) == {"i": True}
    assert oracle_with(ok).check(CONFIGS, [inv(InvariantKind.ISOLATE)]) == {"i": False}
    assert oracle_with(ok).check(CONFIGS, [inv(InvariantKind.WAYPOINT, via="fw")]) == {"i": True}
    assert oracle_with(ok).check(CONFIGS, [inv(InvariantKind.WAYPOINT, via="r9")]) == {"i": False}
    # multipath: every trace must be delivered for reach, none for isolate
    mixed = ok + [trace("NO_ROUTE", ["r1"])]
    assert oracle_with(mixed).check(CONFIGS, [inv(InvariantKind.REACH)]) == {"i": False}
    assert oracle_with(mixed).check(CONFIGS, [inv(InvariantKind.ISOLATE)]) == {"i": False}


def test_flow_enters_at_the_source_interface_and_ip_uses_gre():
    o = oracle_with([trace("ACCEPTED", ["r1"])])
    o.check(CONFIGS, [inv(InvariantKind.REACH, headers={"protocol": "ip"})])
    call = o._client.calls[0]
    assert call["startLocation"] == "@enter(r1[Gi0/0])"
    assert call["headers"]["ipProtocols"] == ["GRE"] and "dstPorts" not in call["headers"]


def test_unlocated_source_mirrors_reference():
    o = oracle_with([])
    res = o.check(CONFIGS, [inv(InvariantKind.ISOLATE, src="192.168.9.10"), ])
    assert res == {"i": True} and o.last_unlocated == ["i"]
    assert oracle_with([]).check(CONFIGS, [inv(InvariantKind.REACH, src="192.168.9.10")]) == {"i": False}


def test_explain_denied_names_device_acl_and_line():
    o = oracle_with([trace("DENIED_IN", ["r1"], denied="r1")])
    d = o.explain_failure(CONFIGS, inv(InvariantKind.REACH, headers={"protocol": "tcp", "dst_port": 22}))
    assert (d.passed, d.blocking_device, d.blocking_acl, d.blocking_line) == (False, "r1", "IN", 1)
    assert d.rule_text == "deny tcp any any eq 22"


def test_explain_implicit_deny_and_no_route():
    o = oracle_with([trace("DENIED_IN", ["r1"], denied="r1")], line="no-match")
    d = o.explain_failure(CONFIGS, inv(InvariantKind.REACH))
    assert d.blocking_acl == "IN" and d.blocking_line is None and "implicit deny" in d.reason
    d = oracle_with([trace("NO_ROUTE", ["r1"])]).explain_failure(CONFIGS, inv(InvariantKind.REACH))
    assert d.blocking_device == "r1" and "NO_ROUTE" in d.reason


def test_snapshot_reused_while_configs_unchanged():
    o = oracle_with([trace("ACCEPTED", ["r1"])])
    loads = []
    o._client.init_snapshot = lambda *a, **kw: loads.append(1)
    o.check(CONFIGS, [inv(InvariantKind.REACH)])
    o.check(CONFIGS, [inv(InvariantKind.ISOLATE)])
    o.check({**CONFIGS, "fw": CONFIGS["fw"] + "!\n"}, [inv(InvariantKind.REACH)])
    assert len(loads) == 2
