"""Which VRF syntax does Batfish accept?  Needs the Batfish container running.

Tries several Cisco IOS VRF variants on one edge router of the Abilene bed, and for each prints Batfish's file
parse status, its parse warnings (the exact lines it does not understand), and - if the file parses - whether the
guest network is isolated as intended.  Paste the whole output back.
    python3 scripts/diagnose_vrf_batfish.py
"""
import os, re, shutil, sys, tempfile
ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, ROOT)
import pandas as pd
pd.set_option("display.width", 250); pd.set_option("display.max_colwidth", 120); pd.set_option("display.max_rows", 50)
from pybatfish.client.session import Session
from pybatfish.datamodel.flow import HeaderConstraints, PathConstraints
from cuc.testbed import get_testbed

tb = get_testbed("abilene")
dev = tb.host_dev[1]
guest, other = tb.guests[1], tb.subnets[2]
base = tb.baseline[dev]
gif = re.search(r"^interface (\S+)\n description guests", base, re.M).group(1)


def with_iface_line(text, line):
    m = re.search(rf"^interface {re.escape(gif)}\n((?: .*\n)*)", text, re.M)
    body = m.group(1).replace(" ip address ", f" {line}\n ip address ", 1)
    return text[:m.start(1)] + body + text[m.end(1):]


VARIANTS = {
    "A: ip vrf + rd, 'ip vrf forwarding'": (with_iface_line(base, "ip vrf forwarding GUEST") + "!\nip vrf GUEST\n rd 65000:1\n!\n"),
    "B: ip vrf (no rd), 'ip vrf forwarding'": (with_iface_line(base, "ip vrf forwarding GUEST") + "!\nip vrf GUEST\n!\n"),
    "C: vrf definition + rd + af ipv4, 'vrf forwarding'": (with_iface_line(base, "vrf forwarding GUEST") +
        "!\nvrf definition GUEST\n rd 65000:1\n !\n address-family ipv4\n exit-address-family\n!\n"),
    "D: vrf definition + af ipv4 (no rd), 'vrf forwarding'": (with_iface_line(base, "vrf forwarding GUEST") +
        "!\nvrf definition GUEST\n address-family ipv4\n exit-address-family\n!\n"),
    "E: vrf statements placed BEFORE the interfaces (A's syntax)": base.replace("!\ninterface", "!\nip vrf GUEST\n rd 65000:1\n!\ninterface", 1)
        .replace(f"interface {gif}\n description guests\n ip address", f"interface {gif}\n description guests\n ip vrf forwarding GUEST\n ip address", 1),
}

bf = Session(host="localhost")
bf.set_network("cuc_vrfdiag")
src_ip = str(__import__("ipaddress").ip_network(guest).network_address + 10)
dst_ip = str(__import__("ipaddress").ip_network(other).network_address + 10)
for label, text in VARIANTS.items():
    d = tempfile.mkdtemp()
    os.makedirs(os.path.join(d, "configs"))
    for dv, t in tb.baseline.items():
        open(os.path.join(d, "configs", f"{dv}.cfg"), "w").write(text if dv == dev else t)
    bf.init_snapshot(d, name="v", overwrite=True)
    shutil.rmtree(d, ignore_errors=True)
    print("=" * 100); print(label)
    st = bf.q.fileParseStatus().answer().frame()
    print("  parse status of", dev, ":", list(st[st["File_Name"].str.contains(f"/{dev}.cfg")]["Status"]))
    try:
        w = bf.q.parseWarning().answer().frame()
        w = w[w["Filename"].astype(str).str.contains(f"/{dev}.cfg")] if len(w) else w
        print("  parse warnings:" if len(w) else "  parse warnings: none")
        if len(w):
            print(w.to_string(index=False))
    except Exception as e:
        print("  parseWarning question failed:", e)
    try:
        ii = bf.q.initIssues().answer().frame()
        if len(ii):
            print("  init issues:"); print(ii.head(10).to_string(index=False))
    except Exception as e:
        print("  initIssues question failed:", e)
    try:
        ans = bf.q.reachability(pathConstraints=PathConstraints(startLocation=f"@enter({dev}[{gif}])"),
                                headers=HeaderConstraints(srcIps=src_ip, dstIps=dst_ip, ipProtocols=["TCP"], dstPorts="80"),
                                actions="SUCCESS").answer().frame()
        print(f"  guest {guest} -> {other} tcp/80 reachable according to Batfish: {len(ans) > 0}   (intended: False)")
    except Exception as e:
        print("  reachability failed:", e)
