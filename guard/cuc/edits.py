"""Text-level config edits shared by the oracle suite, generator and simulated models."""
from __future__ import annotations

import re
from typing import Dict, List

from .iosmodel import (AclLine, PrefixLine, bind_ospf_distribute_list, parse_acl_line, parse_config,
                       replace_acl_block, replace_prefix_list)

Texts = Dict[str, str]


def acl_lines(text: str, dev: str, acl: str) -> List[AclLine]:
    return list(parse_config(text, dev).acls[acl])


def set_acl(texts: Texts, dev: str, acl: str, lines: List[AclLine]) -> Texts:
    out = dict(texts)
    out[dev] = replace_acl_block(texts[dev], acl, sorted(lines, key=lambda l: l.seq))
    return out


def add_acl_line(texts: Texts, dev: str, acl: str, line: str) -> Texts:
    new = parse_acl_line(line)
    lines = acl_lines(texts[dev], dev, acl) if acl in parse_config(texts[dev], dev).acls else []
    return set_acl(texts, dev, acl, lines + [new])


def remove_acl_line(texts: Texts, dev: str, acl: str, seq: int) -> Texts:
    lines = [l for l in acl_lines(texts[dev], dev, acl) if l.seq != seq]
    return set_acl(texts, dev, acl, lines)


def resequence(lines: List[AclLine], step: int = 10) -> List[AclLine]:
    srt = sorted(lines, key=lambda l: l.seq)
    return [AclLine(step * (i + 1), l.action, l.proto, l.src, l.dst, l.dport) for i, l in enumerate(srt)]


def sub(texts: Texts, dev: str, old: str, new: str, count: int = 1) -> Texts:
    if old not in texts[dev]:
        raise ValueError(f"edit anchor not found in {dev}: {old!r}")
    out = dict(texts)
    out[dev] = texts[dev].replace(old, new, count)
    return out


def diff_footprint(old: Texts, new: Texts) -> set:
    """Config structures that changed: {(device, kind, name)}  kind in acl|iface|ospf|other."""
    fp = set()
    for dev in new:
        if old.get(dev) == new[dev]:
            continue
        try:
            a, b = parse_config(old[dev], dev), parse_config(new[dev], dev)
        except Exception:
            fp.add((dev, "other", "*"))
            continue
        for n in set(a.acls) | set(b.acls):
            if a.acls.get(n) != b.acls.get(n):
                fp.add((dev, "acl", n))
        for n in set(a.interfaces) | set(b.interfaces):
            if a.interfaces.get(n) != b.interfaces.get(n):
                fp.add((dev, "iface", n))
        for n in set(a.prefix_lists) | set(b.prefix_lists):
            if a.prefix_lists.get(n) != b.prefix_lists.get(n):
                fp.add((dev, "prefixlist", n))
        if a.vrfs != b.vrfs:
            fp.add((dev, "vrf", "*"))
        if a.ospf_networks != b.ospf_networks or a.ospf_dist_in != b.ospf_dist_in:
            fp.add((dev, "ospf", "*"))
    return fp


def prefix_lines(text: str, dev: str, name: str) -> List[PrefixLine]:
    cfg = parse_config(text, dev)
    return list(cfg.prefix_lists.get(name, []))


def set_prefix_list(texts: Texts, dev: str, name: str, lines: List[PrefixLine], bind: bool = False) -> Texts:
    out = dict(texts)
    t = replace_prefix_list(texts[dev], name, sorted(lines, key=lambda l: l.seq))
    out[dev] = bind_ospf_distribute_list(t, name) if bind else t
    return out
