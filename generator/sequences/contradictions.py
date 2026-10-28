"""Semantic contradiction detection for network invariants.

Ensures that new policy intents never contradict previously established invariants
(e.g., requesting reachability between endpoints that were explicitly isolated,
or isolating endpoints required to maintain reachability).
"""

from agent.contract.models import Invariant, InvariantKind


def is_contradictory(new_inv: Invariant, existing_invariants: list[Invariant]) -> bool:
    """Check if a new invariant semantically contradicts any existing invariant.

    Rules:
    - An ISOLATE invariant contradicts an existing REACH invariant on the exact same src, dst, proto, and port.
    - A REACH invariant contradicts an existing ISOLATE invariant on the exact same src, dst, proto, and port.
    - A WAYPOINT invariant contradicts an existing ISOLATE invariant on the exact same src, dst, proto, and port.
    """
    new_proto = str(new_inv.headers.get("protocol", "ip")).lower()
    new_port = new_inv.headers.get("dst_port")

    for old_inv in existing_invariants:
        old_proto = str(old_inv.headers.get("protocol", "ip")).lower()
        old_port = old_inv.headers.get("dst_port")

        # Check endpoint identity
        same_endpoints = (new_inv.source == old_inv.source and new_inv.destination == old_inv.destination)
        if not same_endpoints:
            continue

        # Check protocol compatibility
        proto_overlap = (new_proto == "ip" or old_proto == "ip" or new_proto == old_proto)
        port_overlap = (new_port is None or old_port is None or new_port == old_port)

        if proto_overlap and port_overlap:
            # Direct contradiction cases:
            if new_inv.kind == InvariantKind.REACH and old_inv.kind == InvariantKind.ISOLATE:
                return True
            if new_inv.kind == InvariantKind.ISOLATE and old_inv.kind == InvariantKind.REACH:
                return True
            if new_inv.kind == InvariantKind.WAYPOINT and old_inv.kind == InvariantKind.ISOLATE:
                return True
            if new_inv.kind == InvariantKind.ISOLATE and old_inv.kind == InvariantKind.WAYPOINT:
                return True
            # Waypoint with different required via node
            if new_inv.kind == InvariantKind.WAYPOINT and old_inv.kind == InvariantKind.WAYPOINT:
                if new_inv.via != old_inv.via:
                    return True

    return False
