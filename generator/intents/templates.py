"""Intent templates for generating policy changes and verified reference gold patches."""

import ipaddress
import random
from typing import Literal
from agent.contract.models import Intent, IntentType, Invariant, InvariantKind
from generator.intents.paraphrases import get_paraphrases
from generator.topology.spec import TopologySpec


def format_gold_patch_as_model_output(gold_patch: dict[str, str]) -> str:
    """Format gold patch dictionary as strict model output markdown text."""
    blocks: list[str] = []
    for dev_name in sorted(gold_patch.keys()):
        cfg = gold_patch[dev_name].strip()
        blocks.append(f"```{dev_name}\n{cfg}\n```")
    return "\n\n".join(blocks)


def _insert_acl_rule_into_config(config: str, acl_name: str, rule_line: str, insert_at_top: bool = True) -> str:
    """Insert an ACL rule line into a device configuration."""
    lines = config.splitlines()
    out_lines: list[str] = []
    in_acl = False
    inserted = False

    header = f"ip access-list extended {acl_name}"

    for line in lines:
        stripped = line.strip()
        if stripped == header:
            in_acl = True
            out_lines.append(line)
            if insert_at_top and not inserted:
                out_lines.append(f" {rule_line}")
                inserted = True
            continue

        if in_acl:
            # End of ACL block reached on exclamation mark or unindented command
            if stripped == "!" or not line.startswith(" "):
                if not inserted:
                    out_lines.append(f" {rule_line}")
                    inserted = True
                in_acl = False
                out_lines.append(line)
                continue

        out_lines.append(line)

    if not inserted:
        # ACL didn't exist yet, append it
        out_lines.append("!")
        out_lines.append(header)
        out_lines.append(f" {rule_line}")
        out_lines.append("!")

    return "\n".join(out_lines).strip() + "\n"


def _remove_route_from_config(config: str, prefix_net: ipaddress.IPv4Network) -> str:
    """Remove static routes targeting a specific network prefix."""
    lines = config.splitlines()
    out_lines: list[str] = []
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("ip route "):
            # Check if line matches prefix
            parts = stripped.split()
            # ip route [vrf X] <pfx> <mask> <nh>
            net_idx = 4 if len(parts) >= 6 and parts[2] == "vrf" else 2
            mask_idx = net_idx + 1
            if mask_idx < len(parts):
                try:
                    line_net = ipaddress.IPv4Network(f"{parts[net_idx]}/{parts[mask_idx]}", strict=False)
                    if line_net == prefix_net or prefix_net.subnet_of(line_net):
                        # Filter this route out
                        continue
                except Exception:
                    pass
        out_lines.append(line)
    return "\n".join(out_lines).strip() + "\n"


def generate_acl_permit_step(
    spec: TopologySpec,
    current_configs: dict[str, str],
    step_idx: int,
    sequence_id: str,
    split: Literal["train", "test"],
    rng: random.Random,
    src_ip: str | None = None,
    dst_ip: str | None = None,
    port: int | None = None,
) -> tuple[Intent, Invariant, dict[str, str]]:
    """Generate an ACL_PERMIT change step, invariant, and gold patch."""
    # Pick target edge device that hosts global subnets
    eligible_devs = [
        name for name, node in sorted(spec.nodes.items())
        if node.role.value == "edge" and any(s.vrf is None for s in node.host_subnets)
    ]
    target_dev = rng.choice(eligible_devs) if eligible_devs else "fw1"

    if port is None:
        # Ports currently denied or restricted: 8080, 8443, 22, 23, 3389
        port = rng.choice([8080, 8443, 8888, 9000, 2222])

    if src_ip is None or dst_ip is None:
        target_node = spec.nodes[target_dev]
        s1 = next(s for s in target_node.host_subnets if s.vrf is None)
        other_hsubs = [
            s for n in spec.nodes.values()
            for s in n.host_subnets
            if s.vrf is None and s != s1
        ]
        s2 = rng.choice(other_hsubs)
        src_ip = s1.gateway_ip[:-1] + str(rng.randint(10, 50))
        dst_ip = s2.gateway_ip[:-1] + str(rng.randint(10, 50))

    proto = "tcp"
    rule_line = f"permit {proto} host {src_ip} host {dst_ip} eq {port}"

    new_cfg = _insert_acl_rule_into_config(current_configs[target_dev], "EDGE_IN", rule_line, insert_at_top=True)
    gold_patch = {target_dev: new_cfg}

    inv_id = f"{sequence_id}_step_{step_idx:03d}_permit_tcp_{port}"
    invariant = Invariant(
        id=inv_id,
        kind=InvariantKind.REACH,
        source=src_ip,
        destination=dst_ip,
        headers={"protocol": proto, "dst_port": port},
        created_at_step=step_idx,
    )

    templates = get_paraphrases(IntentType.ACL_PERMIT, split)
    template = rng.choice(templates)
    text = template.format(protocol=proto.upper(), src=src_ip, dst=dst_ip, port=port, device=target_dev)

    intent = Intent(
        id=f"{sequence_id}_intent_{step_idx:03d}",
        sequence_id=sequence_id,
        step=step_idx,
        type=IntentType.ACL_PERMIT,
        params={"device": target_dev, "protocol": proto, "src": src_ip, "dst": dst_ip, "port": port},
        text=text,
        adds_invariant=inv_id,
        gold_patch=gold_patch,
    )

    return intent, invariant, gold_patch


def generate_acl_deny_step(
    spec: TopologySpec,
    current_configs: dict[str, str],
    step_idx: int,
    sequence_id: str,
    split: Literal["train", "test"],
    rng: random.Random,
    src_ip: str | None = None,
    dst_ip: str | None = None,
    port: int | None = None,
) -> tuple[Intent, Invariant, dict[str, str]]:
    """Generate an ACL_DENY change step, invariant, and gold patch."""
    # Pick target edge device that hosts global subnets
    eligible_devs = [
        name for name, node in sorted(spec.nodes.items())
        if node.role.value == "edge" and any(s.vrf is None for s in node.host_subnets)
    ]
    target_dev = rng.choice(eligible_devs) if eligible_devs else "fw1"

    if port is None:
        port = rng.choice([21, 69, 110, 143, 993, 8000])

    if src_ip is None or dst_ip is None:
        target_node = spec.nodes[target_dev]
        s1 = next(s for s in target_node.host_subnets if s.vrf is None)
        other_hsubs = [
            s for n in spec.nodes.values()
            for s in n.host_subnets
            if s.vrf is None and s != s1
        ]
        s2 = rng.choice(other_hsubs)
        src_ip = s1.gateway_ip[:-1] + str(rng.randint(10, 50))
        dst_ip = s2.gateway_ip[:-1] + str(rng.randint(10, 50))

    proto = "tcp"
    rule_line = f"deny {proto} host {src_ip} host {dst_ip} eq {port}"

    new_cfg = _insert_acl_rule_into_config(current_configs[target_dev], "EDGE_IN", rule_line, insert_at_top=True)
    gold_patch = {target_dev: new_cfg}

    inv_id = f"{sequence_id}_step_{step_idx:03d}_deny_tcp_{port}"
    invariant = Invariant(
        id=inv_id,
        kind=InvariantKind.ISOLATE,
        source=src_ip,
        destination=dst_ip,
        headers={"protocol": proto, "dst_port": port},
        created_at_step=step_idx,
    )

    templates = get_paraphrases(IntentType.ACL_DENY, split)
    template = rng.choice(templates)
    text = template.format(protocol=proto.upper(), src=src_ip, dst=dst_ip, port=port, device=target_dev)

    intent = Intent(
        id=f"{sequence_id}_intent_{step_idx:03d}",
        sequence_id=sequence_id,
        step=step_idx,
        type=IntentType.ACL_DENY,
        params={"device": target_dev, "protocol": proto, "src": src_ip, "dst": dst_ip, "port": port},
        text=text,
        adds_invariant=inv_id,
        gold_patch=gold_patch,
    )

    return intent, invariant, gold_patch


def generate_vrf_isolate_step(
    spec: TopologySpec,
    current_configs: dict[str, str],
    step_idx: int,
    sequence_id: str,
    split: Literal["train", "test"],
    rng: random.Random,
) -> tuple[Intent, Invariant, dict[str, str]]:
    """Generate a VRF_ISOLATE change step, invariant, and gold patch."""
    vrf_nodes = [name for name, node in sorted(spec.nodes.items()) if len(node.vrfs) >= 2]
    target_dev = rng.choice(vrf_nodes) if vrf_nodes else sorted(spec.nodes.keys())[0]

    vrf_a, vrf_b = spec.vrfs[0], spec.vrfs[1]
    node = spec.nodes[target_dev]

    h_a = next((s for s in node.host_subnets if s.vrf == vrf_a), None)
    h_b = next((s for s in node.host_subnets if s.vrf == vrf_b), None)

    src_ip = h_a.gateway_ip[:-1] + "20" if h_a else "10.100.1.20"
    dst_ip = h_b.gateway_ip[:-1] + "20" if h_b else "10.200.1.20"

    # Enforce isolation: ensure deny rule exists in ingress ACL for the foreign VRF subnet
    deny_rule = f"deny ip {h_a.network_cidr if h_a else '10.100.1.0/24'} {h_b.network_cidr if h_b else '10.200.1.0/24'}"
    new_cfg = _insert_acl_rule_into_config(current_configs[target_dev], "EDGE_IN", deny_rule, insert_at_top=True)
    gold_patch = {target_dev: new_cfg}

    inv_id = f"{sequence_id}_step_{step_idx:03d}_vrf_isolate_{vrf_a}_{vrf_b}"
    invariant = Invariant(
        id=inv_id,
        kind=InvariantKind.ISOLATE,
        source=src_ip,
        destination=dst_ip,
        headers={"protocol": "tcp", "dst_port": 80},
        created_at_step=step_idx,
    )

    templates = get_paraphrases(IntentType.VRF_ISOLATE, split)
    template = rng.choice(templates)
    text = template.format(src=src_ip, vrf_src=vrf_a, dst=dst_ip, vrf_dst=vrf_b, device=target_dev)

    intent = Intent(
        id=f"{sequence_id}_intent_{step_idx:03d}",
        sequence_id=sequence_id,
        step=step_idx,
        type=IntentType.VRF_ISOLATE,
        params={"device": target_dev, "vrf_src": vrf_a, "vrf_dst": vrf_b, "src": src_ip, "dst": dst_ip},
        text=text,
        adds_invariant=inv_id,
        gold_patch=gold_patch,
    )

    return intent, invariant, gold_patch


def generate_prefix_filter_step(
    spec: TopologySpec,
    current_configs: dict[str, str],
    step_idx: int,
    sequence_id: str,
    split: Literal["train", "test"],
    rng: random.Random,
) -> tuple[Intent, Invariant, dict[str, str]]:
    """Generate a PREFIX_FILTER change step, invariant, and gold patch."""
    edge_nodes = [name for name, node in sorted(spec.nodes.items()) if node.role.value == "edge"]
    target_dev = rng.choice(edge_nodes)

    # Pick an unassigned prefix to filter (e.g. 192.168.x.0/24 or 172.16.x.0/24)
    rand_octet = rng.randint(10, 200)
    filtered_prefix = f"192.168.{rand_octet}.0/24"
    src_ip = f"192.168.{rand_octet}.10"
    dst_ip = spec.nodes[target_dev].host_subnets[0].gateway_ip if spec.nodes[target_dev].host_subnets else "10.0.0.1"

    # Add prefix-list deny entry and drop route
    pl_rule = f"ip prefix-list PL_FILTER seq {step_idx * 10 + 5} deny {filtered_prefix} le 32"
    rm_rule = f"route-map RM_FILTER permit {step_idx * 10}\n match ip address prefix-list PL_FILTER"

    cfg = current_configs[target_dev]
    cfg = _remove_route_from_config(cfg, ipaddress.IPv4Network(filtered_prefix))
    lines = cfg.splitlines()
    lines.append(pl_rule)
    lines.append("!")
    lines.append(rm_rule)
    lines.append("!")
    new_cfg = "\n".join(lines).strip() + "\n"

    gold_patch = {target_dev: new_cfg}

    inv_id = f"{sequence_id}_step_{step_idx:03d}_prefix_filter_{rand_octet}"
    invariant = Invariant(
        id=inv_id,
        kind=InvariantKind.ISOLATE,
        source=src_ip,
        destination=dst_ip,
        headers={"protocol": "ip"},
        created_at_step=step_idx,
    )

    templates = get_paraphrases(IntentType.PREFIX_FILTER, split)
    template = rng.choice(templates)
    text = template.format(prefix=filtered_prefix, device=target_dev)

    intent = Intent(
        id=f"{sequence_id}_intent_{step_idx:03d}",
        sequence_id=sequence_id,
        step=step_idx,
        type=IntentType.PREFIX_FILTER,
        params={"device": target_dev, "prefix": filtered_prefix},
        text=text,
        adds_invariant=inv_id,
        gold_patch=gold_patch,
    )

    return intent, invariant, gold_patch


def generate_waypoint_step(
    spec: TopologySpec,
    current_configs: dict[str, str],
    step_idx: int,
    sequence_id: str,
    split: Literal["train", "test"],
    rng: random.Random,
) -> tuple[Intent, Invariant, dict[str, str]]:
    """Generate a WAYPOINT change step, invariant, and gold patch."""
    fw_name = spec.firewall_node

    hsubs = [s for n in spec.nodes.values() for s in n.host_subnets if s.vrf is None]
    s1 = hsubs[0]
    s2 = hsubs[-1]
    src_ip = s1.gateway_ip[:-1] + str(rng.randint(10, 50))
    dst_ip = s2.gateway_ip[:-1] + str(rng.randint(10, 50))
    port = rng.choice([80, 443, 8080])
    proto = "tcp"

    # Firewall inspection rule in EDGE_IN
    permit_rule = f"permit {proto} host {src_ip} host {dst_ip} eq {port}"
    new_fw_cfg = _insert_acl_rule_into_config(current_configs[fw_name], "EDGE_IN", permit_rule, insert_at_top=True)
    gold_patch = {fw_name: new_fw_cfg}

    inv_id = f"{sequence_id}_step_{step_idx:03d}_waypoint_{port}"
    invariant = Invariant(
        id=inv_id,
        kind=InvariantKind.WAYPOINT,
        source=src_ip,
        destination=dst_ip,
        headers={"protocol": proto, "dst_port": port},
        via=fw_name,
        created_at_step=step_idx,
    )

    templates = get_paraphrases(IntentType.WAYPOINT, split)
    template = rng.choice(templates)
    text = template.format(src=src_ip, dst=dst_ip, waypoint=fw_name, device=fw_name)

    intent = Intent(
        id=f"{sequence_id}_intent_{step_idx:03d}",
        sequence_id=sequence_id,
        step=step_idx,
        type=IntentType.WAYPOINT,
        params={"src": src_ip, "dst": dst_ip, "waypoint": fw_name, "port": port},
        text=text,
        adds_invariant=inv_id,
        gold_patch=gold_patch,
    )

    return intent, invariant, gold_patch
