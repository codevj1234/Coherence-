"""Natural language paraphrases for network intent generation.

Strictly partitions paraphrases into TRAIN and TEST pools to evaluate model generalization
to unseen instructional formulations.
"""

from typing import Literal
from agent.contract.models import IntentType

# Train paraphrase templates (3-4 per intent type)
TRAIN_PARAPHRASES: dict[IntentType, list[str]] = {
    IntentType.ACL_PERMIT: [
        "Permit {protocol} traffic from {src} to {dst} on port {port} on router {device}.",
        "Allow inbound {protocol} connections from {src} to destination {dst} port {port} at {device}.",
        "On {device}, update the access policy to accept {protocol} packets destined for {dst}:{port} originating from {src}.",
        "Grant access for {src} to reach host {dst} via {protocol} on service port {port} on {device}.",
    ],
    IntentType.ACL_DENY: [
        "Block all {protocol} traffic from {src} to {dst} on port {port} on router {device}.",
        "Deny inbound {protocol} access from {src} to destination {dst} port {port} at {device}.",
        "On router {device}, drop {protocol} packets from {src} attempting to connect to {dst}:{port}.",
        "Restrict {src} from accessing {dst} on port {port} using {protocol} on {device}.",
    ],
    IntentType.VRF_ISOLATE: [
        "Ensure network {src} in VRF {vrf_src} remains strictly isolated from network {dst} in VRF {vrf_dst}.",
        "Isolate VRF {vrf_src} subnet {src} from communicating with VRF {vrf_dst} subnet {dst} on router {device}.",
        "Prevent any route leakage or transit traffic between VRF {vrf_src} ({src}) and VRF {vrf_dst} ({dst}) on {device}.",
        "Maintain tenant separation by blocking all traffic between {src} on VRF {vrf_src} and {dst} on VRF {vrf_dst}.",
    ],
    IntentType.PREFIX_FILTER: [
        "Apply a prefix filter on {device} to reject routing advertisement for prefix {prefix}.",
        "On {device}, filter out incoming route prefix {prefix} from reaching the forwarding table.",
        "Block routing updates for subnet {prefix} on router {device} using prefix filtering.",
        "Drop routes matching prefix {prefix} on router {device}.",
    ],
    IntentType.WAYPOINT: [
        "Ensure that all traffic from {src} to {dst} is routed through transit firewall {waypoint}.",
        "Require packets from source {src} to destination {dst} to traverse inspection node {waypoint}.",
        "Route traffic between {src} and {dst} via inspection gateway {waypoint} on {device}.",
        "Enforce that network flow from {src} targeting {dst} passes through waypoint {waypoint}.",
    ],
}

# Test paraphrase templates (strictly reserved for test splits)
TEST_PARAPHRASES: dict[IntentType, list[str]] = {
    IntentType.ACL_PERMIT: [
        "Configure router {device} to permit incoming {protocol} requests from source {src} towards target {dst} targeting port {port}.",
        "Ensure {device} allows incoming {src} traffic directed to {dst} for {protocol} port {port}.",
    ],
    IntentType.ACL_DENY: [
        "Prevent {src} from establishing {protocol} sessions with {dst} on port {port} through {device}.",
        "Enforce an access restriction on {device} prohibiting {protocol} traffic from {src} destined to port {port} on {dst}.",
    ],
    IntentType.VRF_ISOLATE: [
        "Enforce strict isolation between virtual routing domain {vrf_src} for {src} and domain {vrf_dst} for {dst} across {device}.",
        "Guarantee that no packet from {src} within VRF {vrf_src} can reach destination {dst} inside VRF {vrf_dst}.",
    ],
    IntentType.PREFIX_FILTER: [
        "Suppress the advertisement of route prefix {prefix} on device {device} using route-map policy.",
        "Filter route prefix {prefix} on router {device} to prevent route installation.",
    ],
    IntentType.WAYPOINT: [
        "Mandate that data flows originating from {src} and addressed to {dst} steer through waypoint {waypoint}.",
        "Direct traffic from subnet {src} to destination {dst} such that it is inspected at node {waypoint}.",
    ],
}


def get_paraphrases(intent_type: IntentType, split: Literal["train", "test"]) -> list[str]:
    """Retrieve the paraphrase pool for the given intent type and data split."""
    if split == "test":
        return TEST_PARAPHRASES[intent_type]
    return TRAIN_PARAPHRASES[intent_type]


PARAPHRASE_POOLS: dict[IntentType, dict[str, list[str]]] = {
    itype: {"train": TRAIN_PARAPHRASES[itype], "test": TEST_PARAPHRASES[itype]}
    for itype in IntentType
}

TEST_RESERVED_PHRASES: dict[IntentType, list[str]] = {
    IntentType.ACL_PERMIT: ["targeting port", "directed to"],
    IntentType.ACL_DENY: ["establishing", "prohibiting"],
    IntentType.VRF_ISOLATE: ["virtual routing domain", "Guarantee that"],
    IntentType.PREFIX_FILTER: ["Suppress the advertisement", "prevent route installation"],
    IntentType.WAYPOINT: ["Mandate that", "steer through", "inspected at node"],
}
