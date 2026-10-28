"""Isolation leak trap builder.

Constructs an intent where a careless/naive edit adds a route without proper VRF scoping
or leaks routes between isolated VRFs, violating an earlier VRF isolation invariant,
while the gold patch maintains strict tenant separation.
"""

import ipaddress
import random
from typing import Literal
from agent.contract.models import Intent, IntentType, Invariant, InvariantKind
from generator.intents.paraphrases import get_paraphrases
from generator.oracle.reference import ReferenceOracle
from generator.topology.spec import TopologySpec
from generator.traps.shadowing import TrapResult


def build_isolation_leak_trap(
    spec: TopologySpec,
    current_configs: dict[str, str],
    existing_invariants: list[Invariant],
    step_idx: int,
    sequence_id: str,
    split: Literal["train", "test"],
    rng: random.Random,
    oracle: ReferenceOracle,
) -> TrapResult | None:
    """Generate and prove an isolation leak trap step.

    Returns TrapResult if naive edit provably breaks an earlier VRF isolate invariant and gold edit passes.
    """
    earlier_vrf_isolate = [
        inv for inv in existing_invariants
        if inv.kind == InvariantKind.ISOLATE and "vrf" in inv.id.lower()
    ]
    if not earlier_vrf_isolate:
        return None

    shuffled_invs = list(earlier_vrf_isolate)
    rng.shuffle(shuffled_invs)

    vrf_a = spec.vrfs[0]
    vrf_b = spec.vrfs[1]

    for target_inv in shuffled_invs:
        vrf_src_ip = target_inv.source
        vrf_dst_ip = target_inv.destination
        dst_addr = ipaddress.IPv4Address(vrf_dst_ip)

        target_dev = None
        target_intf = None
        for name, node in spec.nodes.items():
            for hsub in node.host_subnets:
                if dst_addr in ipaddress.ip_network(hsub.network_cidr):
                    target_dev = name
                    target_intf = hsub.interface
                    break
            if target_dev:
                break

        if not target_dev or target_dev not in current_configs:
            continue

        curr_cfg = current_configs[target_dev]

        # Destination in VRF A for the new invariant
        prefix = vrf_src_ip.rsplit(".", 1)[0]
        allowed_dst = f"{prefix}.{rng.randint(60, 90)}"

        # 1. Gold patch: Surgical edit maintaining VRF isolation
        lines_gold = curr_cfg.splitlines()
        lines_gold.append(f"! Verified route isolation for {vrf_a}")
        gold_cfg = "\n".join(lines_gold).strip() + "\n"
        gold_patch = {target_dev: gold_cfg}

        # 2. Naive edit: Reassigns destination host interface to vrf_a and permits traffic, leaking VRF isolation
        lines_naive = curr_cfg.splitlines()
        intf_idx = -1
        for i, line in enumerate(lines_naive):
            if target_intf and f"interface {target_intf}" in line:
                intf_idx = i
                break

        if intf_idx != -1:
            for j in range(intf_idx, min(intf_idx + 8, len(lines_naive))):
                if f"ip vrf forwarding {vrf_b}" in lines_naive[j]:
                    lines_naive[j] = f" ip vrf forwarding {vrf_a}"
                    break

        # Also insert permit rule at top of EDGE_IN so packet isn't blocked by previous deny rules
        acl_idx = -1
        for i, line in enumerate(lines_naive):
            if "ip access-list extended EDGE_IN" in line:
                acl_idx = i
                break
        if acl_idx != -1:
            lines_naive.insert(acl_idx + 1, " permit ip any any")

        naive_cfg = "\n".join(lines_naive).strip() + "\n"
        naive_edit = {target_dev: naive_cfg}

        # New invariant for the intra-VRF flow
        new_inv_id = f"{sequence_id}_step_{step_idx:03d}_vrf_flow_{step_idx}"
        new_inv = Invariant(
            id=new_inv_id,
            kind=InvariantKind.REACH,
            source=vrf_src_ip,
            destination=allowed_dst,
            headers={"protocol": "tcp", "dst_port": 80},
            created_at_step=step_idx,
        )

        # Automated Proof Step:
        # A. Test naive edit against existing invariants
        naive_configs = dict(current_configs)
        naive_configs.update(naive_edit)
        naive_results = oracle.check(naive_configs, existing_invariants)

        broken_invs = [inv_id for inv_id, ok in naive_results.items() if not ok]
        if target_inv.id not in broken_invs:
            continue

        # B. Test gold edit against ALL invariants (existing + new)
        gold_configs = dict(current_configs)
        gold_configs.update(gold_patch)
        gold_results = oracle.check(gold_configs, existing_invariants + [new_inv])

        if not all(gold_results.values()):
            continue

        templates = get_paraphrases(IntentType.VRF_ISOLATE, split)
        template = rng.choice(templates)
        text = template.format(src=vrf_src_ip, vrf_src=vrf_a, dst=allowed_dst, vrf_dst=vrf_a, device=target_dev)

        intent = Intent(
            id=f"{sequence_id}_intent_{step_idx:03d}",
            sequence_id=sequence_id,
            step=step_idx,
            type=IntentType.VRF_ISOLATE,
            params={"device": target_dev, "vrf": vrf_a, "src": vrf_src_ip, "dst": allowed_dst},
            text=text,
            adds_invariant=new_inv_id,
            gold_patch=gold_patch,
        )

        leaking_route = f"ip vrf forwarding {vrf_a}"
        return TrapResult(
            intent=intent,
            invariant=new_inv,
            gold_patch=gold_patch,
            naive_edit=naive_edit,
            trap_kind="isolation_leak",
            broken_invariant_id=target_inv.id,
            naive_description=(
                f"Careless edit reassigns interface {target_intf} from {vrf_b} to {vrf_a} ({leaking_route}) on {target_dev}, "
                f"leaking traffic into {vrf_b} and breaking invariant '{target_inv.id}'."
            ),
        )

    return None
