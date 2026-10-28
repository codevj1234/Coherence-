"""Topology package providing specs, configs generator, and topology catalog."""

from generator.topology.catalog import TOPOLOGIES, get_topology, list_topologies
from generator.topology.generator import generate_device_config, generate_topology_configs
from generator.topology.spec import HostSubnet, Link, NodeRole, TopologyNode, TopologySpec

__all__ = [
    "TopologySpec",
    "TopologyNode",
    "NodeRole",
    "Link",
    "HostSubnet",
    "TOPOLOGIES",
    "get_topology",
    "list_topologies",
    "generate_device_config",
    "generate_topology_configs",
]
