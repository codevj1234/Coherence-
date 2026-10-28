"""PROVISIONAL: Configuration diffing utility for reference-oracle evaluations.

Analyzes router and firewall configuration diffs to detect dropped or modified
configuration statements, with heuristics to flag unintentional removals unrelated
to the current change intent (e.g., dropped ACL attachments or neighbor statements).
"""

from dataclasses import dataclass, field
import ipaddress
import re
from typing import Any

from agent.contract.models import Intent


@dataclass
class ConfigDiffResult:
    """Differences between baseline and updated device configuration."""

    removed_lines: list[str] = field(default_factory=list)
    added_lines: list[str] = field(default_factory=list)
    unrelated_removed_lines: list[str] = field(default_factory=list)

    @property
    def has_unrelated_removals(self) -> bool:
        """True if any line was removed that has no relation to the intent targets."""
        return len(self.unrelated_removed_lines) > 0


def _normalize_config_lines(config: str) -> list[str]:
    """Split and normalize configuration lines, filtering empty lines and pure comments."""
    lines: list[str] = []
    for raw in config.splitlines():
        stripped = raw.strip()
        # Cisco configs use '!' as separator comments
        if not stripped or stripped == "!":
            continue
        lines.append(stripped)
    return lines


def _extract_intent_tokens_and_networks(
    intent: Intent | dict[str, Any] | None = None,
    *,
    acl: str | None = None,
    interface: str | None = None,
    prefix: str | None = None,
) -> tuple[set[str], list[ipaddress.IPv4Network | ipaddress.IPv6Network]]:
    """Extract string tokens and IP subnet objects from intent parameters."""
    tokens: set[str] = set()
    networks: list[ipaddress.IPv4Network | ipaddress.IPv6Network] = []

    def _add_cand(val: str) -> None:
        cleaned = val.strip()
        if not cleaned:
            return
        tokens.add(cleaned.lower())
        if "/" in cleaned:
            tokens.add(cleaned.split("/")[0].lower())
            try:
                net = ipaddress.ip_network(cleaned, strict=False)
                networks.append(net)
            except ValueError:
                pass
        else:
            try:
                addr = ipaddress.ip_address(cleaned)
                net = ipaddress.ip_network(f"{addr}/32", strict=False)
                networks.append(net)
            except ValueError:
                pass

    for explicit_tok in (acl, interface, prefix):
        if explicit_tok:
            _add_cand(explicit_tok)

    if intent is not None:
        params: dict[str, Any] = {}
        if isinstance(intent, Intent):
            params = intent.params
        elif isinstance(intent, dict):
            params = intent.get("params", {})
        elif hasattr(intent, "params"):
            params = getattr(intent, "params")

        for k, v in params.items():
            if isinstance(v, str):
                _add_cand(v)
            elif isinstance(v, (int, float)):
                tokens.add(str(v).lower())

        text = ""
        if isinstance(intent, Intent):
            text = intent.text
        elif isinstance(intent, dict):
            text = intent.get("text", "")
        elif hasattr(intent, "text"):
            text = getattr(intent, "text")

        if text:
            for match in re.findall(r"\b[A-Za-z0-9_-]{3,}\b", text):
                tokens.add(match.lower())

    return tokens, networks


def diff_configs(
    input_config: str,
    output_config: str,
    intent: Intent | dict[str, Any] | None = None,
    *,
    acl: str | None = None,
    interface: str | None = None,
    prefix: str | None = None,
) -> ConfigDiffResult:
    """Compare baseline input config against modified output config.

    Identifies removed lines, added lines, and applies heuristics to isolate
    removed lines that do not mention the intended ACL, interface, or prefix.

    Args:
        input_config: Original router configuration before modification.
        output_config: Model-generated updated router configuration.
        intent: Optional Intent instance or intent dictionary containing target params.
        acl: Explicit ACL name or identifier targeted by the change.
        interface: Explicit interface name targeted by the change.
        prefix: Explicit prefix/IP targeted by the change.

    Returns:
        ConfigDiffResult with removed, added, and unrelated removed lines.
    """
    input_lines = _normalize_config_lines(input_config)
    output_lines = _normalize_config_lines(output_config)

    output_set = set(output_lines)
    input_set = set(input_lines)

    removed_lines = [line for line in input_lines if line not in output_set]
    added_lines = [line for line in output_lines if line not in input_set]

    tokens, networks = _extract_intent_tokens_and_networks(
        intent=intent,
        acl=acl,
        interface=interface,
        prefix=prefix,
    )

    unrelated_removed: list[str] = []
    for line in removed_lines:
        line_lower = line.lower()
        if not tokens and not networks:
            unrelated_removed.append(line)
            continue

        # Check substring match against tokens
        is_related = False
        for tok in tokens:
            if tok in line_lower:
                is_related = True
                break

        # Check if line contains an IP falling within any intent network
        if not is_related and networks:
            found_ips = re.findall(r"\b(?:\d{1,3}\.){3}\d{1,3}\b", line)
            for ip_str in found_ips:
                try:
                    addr = ipaddress.ip_address(ip_str)
                    # Filter out netmasks like 255.255.255.0
                    if ip_str.startswith("255.") or ip_str.endswith(".255"):
                        continue
                    if any(addr in net for net in networks):
                        is_related = True
                        break
                except ValueError:
                    pass

        if not is_related:
            unrelated_removed.append(line)

    return ConfigDiffResult(
        removed_lines=removed_lines,
        added_lines=added_lines,
        unrelated_removed_lines=unrelated_removed,
    )
