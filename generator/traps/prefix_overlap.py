"""Prefix overlap trap builder.

Constructs an intent where a careless/naive edit uses an overly broad prefix mask
(e.g. /24 or /16 instead of a specific /28 or /32), inadvertently filtering out traffic
required by an earlier reachability invariant, while the gold patch uses the exact narrow prefix.
"""

import ipaddress
import random
from typing import Literal
from agent.contract.models import Intent, IntentType, Invariant, InvariantKind
from generator.intents.paraphrases import get_paraphrases
from generator.intents.templates import _insert_acl_rule_into_config
from generator.oracle.reference import ReferenceOracle
from generator.topology.spec import TopologySpec
from generator.traps.shadowing import TrapResult


def build_prefix_overlap_trap(
    spec: TopologySpec,
    current_configs: dict[str, str],
    existing_invariants: list[Invariant],
    step_idx: int,
    sequence_id: str,
    split: Literal["train", "test"],
    rng: random.Random,
    oracle: ReferenceOracle,
) -> TrapResult | None:
    """Generate and prove a prefix overlap trap step.

    Returns TrapResult if naive edit provably breaks an earlier reach invariant and gold edit passes.
    """
    # Find an earlier REACH invariant
    earlier_reach = [inv for inv in existing_invariants if inv.kind == InvariantKind.REACH]
    if not earlier_reach:
        return None

    target_inv = rng.choice(earlier_reach)
    src_ip = target_inv.source
    dst_ip = target_inv.destination

    # Find the subnet of target_inv source
    src_addr = ipaddress.IPv4Address(src_ip)
    # A neighboring address in the same /24 subnet that is requested to be blocked
    # E.g. .200
    neighbor_src_narrow = str(ipaddress.IPv4Address(int(src_addr) | 0xC0))  # e.g. .192-.255
    neighbor_cidr_narrow = f"{neighbor_src_narrow}/28"
    covering_cidr_broad = f"{src_addr.exploded.rsplit('.', 1)[0]}.0/24"

    target_dev = None
    for name, node in spec.nodes.items():
        for hsub in node.host_subnets:
            if src_addr in ipaddress.ip_network(hsub.network_cidr):
                target_dev = name
                break
        if target_dev:
            break

    if not target_dev or target_dev not in current_configs:
        edge_nodes = [name for name, node in sorted(spec.nodes.items()) if node.role.value == "edge"]
        target_dev = rng.choice(edge_nodes) if edge_nodes else "fw1"

    # 1. Gold patch: Surgically filters only the narrow neighbor prefix (.192/28)
    # in Cisco ACL: deny ip 10.x.x.192 0.0.0.15 any
    narrow_net = ipaddress.IPv4Network(neighbor_cidr_narrow, strict=False)
    wildcard_narrow = "0.0.0.15"
    gold_rule = f"deny ip {narrow_net.network_address} {wildcard_narrow} any"
    gold_cfg = _insert_acl_rule_into_config(current_configs[target_dev], "EDGE_IN", gold_rule, insert_at_top=True)
    gold_patch = {target_dev: gold_cfg}

    # 2. Naive edit: Carelessly filters the entire covering /24, which includes target_inv.source!
    wildcard_broad = "0.0.0.255"
    broad_net = ipaddress.IPv4Network(covering_cidr_broad, strict=False)
    naive_rule = f"deny ip {broad_net.network_address} {wildcard_broad} any"
    naive_cfg = _insert_acl_rule_into_config(current_configs[target_dev], "EDGE_IN", naive_rule, insert_at_top=True)
    naive_edit = {target_dev: naive_cfg}

    # New invariant for the filtered neighbor
    new_inv_id = f"{sequence_id}_step_{step_idx:03d}_filter_overlap"
    new_inv = Invariant(
        id=new_inv_id,
        kind=InvariantKind.ISOLATE,
        source=neighbor_src_narrow,
        destination=dst_ip,
        headers={"protocol": "ip"},
        created_at_step=step_idx,
    )

    # Automated Proof Step:
    # A. Test naive edit against existing invariants
    naive_configs = dict(current_configs)
    naive_configs.update(naive_edit)
    naive_results = oracle.check(naive_configs, existing_invariants)

    broken_invs = [inv_id for inv_id, ok in naive_results.items() if not ok]
    if target_inv.id not in broken_invs:
        return None

    # B. Test gold edit against ALL invariants (existing + new)
    gold_configs = dict(current_configs)
    gold_configs.update(gold_patch)
    gold_results = oracle.check(gold_configs, existing_invariants + [new_inv])

    if not all(gold_results.values()):
        return None

    templates = get_paraphrases(IntentType.ACL_DENY, split)
    template = rng.choice(templates)
    text = template.format(
        protocol="IP",
        src=neighbor_cidr_narrow,
        dst="any destination",
        port="any",
        device=target_dev,
    )

    intent = Intent(
        id=f"{sequence_id}_intent_{step_idx:03d}",
        sequence_id=sequence_id,
        step=step_idx,
        type=IntentType.ACL_DENY,
        params={"device": target_dev, "src_prefix": neighbor_cidr_narrow, "protocol": "ip"},
        text=text,
        adds_invariant=new_inv_id,
        gold_patch=gold_patch,
    )

    return TrapResult(
        intent=intent,
        invariant=new_inv,
        gold_patch=gold_patch,
        naive_edit=naive_edit,
        trap_kind="prefix_overlap",
        broken_invariant_id=target_inv.id,
        naive_description=(
            f"Careless edit applies broad covering filter '{naive_rule}' to {target_dev} EDGE_IN, "
            f"accidentally blocking earlier required source '{src_ip}' and breaking invariant '{target_inv.id}'."
        ),
    )
