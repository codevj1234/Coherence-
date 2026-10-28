"""Shadowing trap builder.

Constructs an intent where a careless/naive edit places a broad permit rule above
an earlier deny rule in an ACL, silently breaking an earlier isolation invariant,
while the gold patch uses a surgical host/port rule or appends below the deny rule.
"""

from dataclasses import dataclass
import random
from typing import Literal
from agent.contract.models import Intent, IntentType, Invariant, InvariantKind
from generator.intents.paraphrases import get_paraphrases
from generator.intents.templates import _insert_acl_rule_into_config
from generator.oracle.reference import ReferenceOracle
from generator.topology.spec import TopologySpec


@dataclass
class TrapResult:
    """Outcome of generating a verified trap step."""

    intent: Intent
    invariant: Invariant
    gold_patch: dict[str, str]
    naive_edit: dict[str, str]
    trap_kind: str
    broken_invariant_id: str
    naive_description: str


def build_shadowing_trap(
    spec: TopologySpec,
    current_configs: dict[str, str],
    existing_invariants: list[Invariant],
    step_idx: int,
    sequence_id: str,
    split: Literal["train", "test"],
    rng: random.Random,
    oracle: ReferenceOracle,
) -> TrapResult | None:
    """Generate and prove a shadowing trap step.

    Returns TrapResult if naive edit provably breaks an earlier invariant and gold edit passes,
    or None if no valid trap could be constructed.
    """
    # Find an earlier ISOLATE invariant with dst_port
    earlier_isolate = [
        inv for inv in existing_invariants
        if inv.kind == InvariantKind.ISOLATE and inv.headers.get("dst_port") is not None
    ]
    if not earlier_isolate:
        return None

    target_inv = rng.choice(earlier_isolate)
    blocked_port = target_inv.headers["dst_port"]
    proto = target_inv.headers.get("protocol", "tcp")

    # Find device enforcing the isolation rule (firewall or ingress router)
    fw_name = next((name for name, node in spec.nodes.items() if node.role.value == "firewall"), None)
    if fw_name and fw_name in current_configs and f"deny {proto} any any eq {blocked_port}" in current_configs[fw_name]:
        target_dev = fw_name
    else:
        target_dev = None
        try:
            import ipaddress
            src_addr = ipaddress.ip_address(target_inv.source)
            for name, node in spec.nodes.items():
                for hsub in node.host_subnets:
                    if src_addr in ipaddress.ip_network(hsub.network_cidr):
                        target_dev = name
                        break
                if target_dev:
                    break
        except Exception:
            pass

        if not target_dev or target_dev not in current_configs:
            edge_nodes = [name for name, node in sorted(spec.nodes.items()) if node.role.value == "edge"]
            target_dev = rng.choice(edge_nodes) if edge_nodes else "fw1"

    # Intent requests access for a specific separate host on that port in the same source subnet
    target_node = spec.nodes.get(target_dev)
    if target_node and target_node.host_subnets:
        src_hsub = next((s for s in target_node.host_subnets if s.vrf is None), target_node.host_subnets[0])
        prefix = src_hsub.gateway_ip.rsplit(".", 1)[0]
        allowed_src = f"{prefix}.{rng.randint(60, 90)}"
    else:
        prefix = target_inv.source.rsplit(".", 1)[0]
        allowed_src = f"{prefix}.{rng.randint(60, 90)}"

    # Pick destination in another global subnet
    other_hsubs = [
        s for n in spec.nodes.values()
        for s in n.host_subnets
        if s.vrf is None and not s.gateway_ip.startswith(prefix + ".")
    ]
    if other_hsubs:
        dst_hsub = rng.choice(other_hsubs)
        allowed_dst = f"{dst_hsub.gateway_ip.rsplit('.', 1)[0]}.{rng.randint(60, 90)}"
    else:
        allowed_dst = target_inv.destination

    # 1. Gold patch: Surgical rule permitting only the specific allowed host
    surgical_rule = f"permit {proto} host {allowed_src} host {allowed_dst} eq {blocked_port}"
    gold_cfg = _insert_acl_rule_into_config(current_configs[target_dev], "EDGE_IN", surgical_rule, insert_at_top=True)
    gold_patch = {target_dev: gold_cfg}

    # 2. Naive edit: Broad permit rule that shadows the earlier deny for all hosts on that port!
    naive_rule = f"permit {proto} any any eq {blocked_port}"
    naive_cfg = _insert_acl_rule_into_config(current_configs[target_dev], "EDGE_IN", naive_rule, insert_at_top=True)
    naive_edit = {target_dev: naive_cfg}

    # New invariant for the new allowed flow
    new_inv_id = f"{sequence_id}_step_{step_idx:03d}_permit_shadow_{blocked_port}"
    new_inv = Invariant(
        id=new_inv_id,
        kind=InvariantKind.REACH,
        source=allowed_src,
        destination=allowed_dst,
        headers={"protocol": proto, "dst_port": blocked_port},
        created_at_step=step_idx,
    )

    # Automated Proof Step:
    # A. Test naive edit against existing invariants
    naive_configs = dict(current_configs)
    naive_configs.update(naive_edit)
    naive_results = oracle.check(naive_configs, existing_invariants)

    broken_invs = [inv_id for inv_id, ok in naive_results.items() if not ok]
    if target_inv.id not in broken_invs:
        # Naive edit did not break the intended invariant; discard
        return None

    # B. Test gold edit against ALL invariants (existing + new)
    gold_configs = dict(current_configs)
    gold_configs.update(gold_patch)
    gold_results = oracle.check(gold_configs, existing_invariants + [new_inv])

    if not all(gold_results.values()):
        # Gold edit failed one or more invariants; discard
        return None

    templates = get_paraphrases(IntentType.ACL_PERMIT, split)
    template = rng.choice(templates)
    text = template.format(protocol=proto.upper(), src=allowed_src, dst=allowed_dst, port=blocked_port, device=target_dev)

    intent = Intent(
        id=f"{sequence_id}_intent_{step_idx:03d}",
        sequence_id=sequence_id,
        step=step_idx,
        type=IntentType.ACL_PERMIT,
        params={"device": target_dev, "protocol": proto, "src": allowed_src, "dst": allowed_dst, "port": blocked_port},
        text=text,
        adds_invariant=new_inv_id,
        gold_patch=gold_patch,
    )

    return TrapResult(
        intent=intent,
        invariant=new_inv,
        gold_patch=gold_patch,
        naive_edit=naive_edit,
        trap_kind="shadowing",
        broken_invariant_id=target_inv.id,
        naive_description=(
            f"Careless edit prepends broad permit rule '{naive_rule}' to {target_dev} EDGE_IN, "
            f"shadowing earlier deny rule and breaking invariant '{target_inv.id}'."
        ),
    )
