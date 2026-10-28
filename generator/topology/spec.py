"""Topology data specifications and models for network generation."""

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Literal
from agent.contract.models import Invariant


class NodeRole(StrEnum):
    """Functional role of a node in the synthetic topology."""

    EDGE = "edge"
    CORE = "core"
    LEAF = "leaf"
    FIREWALL = "firewall"


@dataclass(frozen=True)
class HostSubnet:
    """LAN host subnet attached to a leaf or edge router."""

    interface: str
    network_cidr: str
    gateway_ip: str
    netmask: str
    vrf: str | None = None


@dataclass(frozen=True)
class Link:
    """Point-to-point /30 link connecting two routers."""

    node1: str
    node2: str
    intf1: str
    intf2: str
    subnet_cidr: str
    ip1: str
    ip2: str
    netmask: str = "255.255.255.252"
    vrf: str | None = None


@dataclass(frozen=True)
class TopologyNode:
    """Node specification in the network graph."""

    name: str
    role: NodeRole
    host_subnets: list[HostSubnet] = field(default_factory=list)
    vrfs: list[str] = field(default_factory=list)


@dataclass
class TopologySpec:
    """Full specification of a synthetic network topology."""

    id: str
    split: Literal["train", "test"]
    nodes: dict[str, TopologyNode]
    links: list[Link]
    firewall_node: str
    vrfs: list[str] = field(default_factory=lambda: ["RED", "BLUE"])
    baseline_invariants: list[Invariant] = field(default_factory=list)

    @property
    def inventory(self) -> list[str]:
        """Sorted list of canonical device names in this topology."""
        return sorted(self.nodes.keys())
