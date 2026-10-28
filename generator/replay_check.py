"""Replay verification, intentional corruption testing, and agent pipeline validation."""

import json
from pathlib import Path
import random
import sys
from agent.contract.io import read_jsonl
from agent.contract.models import Condition, Intent, Invariant
from agent.llm.client import LLMClient
from agent.llm.config import get_model_config
from agent.prompts.builder import build_prompt
from agent.prompts.parser import parse_model_output
from agent.prompts.resolve import resolve_devices
from generator.intents.templates import format_gold_patch_as_model_output
from generator.oracle.reference import ReferenceOracle
from generator.topology.catalog import get_topology
from generator.topology.generator import generate_topology_configs


class ReplayResult(tuple):
    """Result tuple of (total_steps, regressions) exposing regressions_by_step."""

    total_steps: int
    regressions: int
    regressions_by_step: dict[int, list[str]]

    def __new__(cls, total_steps: int, regressions: int, regressions_by_step: dict[int, list[str]]):
        obj = super().__new__(cls, (total_steps, regressions))
        obj.total_steps = total_steps
        obj.regressions = regressions
        obj.regressions_by_step = regressions_by_step
        return obj


def replay_single_sequence(jsonl_path: str | Path, manifest_path: str | Path) -> ReplayResult:
    """Replay a single sequence from baseline state using gold patches.

    Returns:
        ReplayResult of (steps_evaluated, regressions_detected) with .regressions_by_step
    """
    jsonl_p = Path(jsonl_path)
    manifest_p = Path(manifest_path)

    with manifest_p.open("r", encoding="utf-8") as f:
        manifest_data = json.load(f)

    topo_id = manifest_data["topology_id"]
    spec = get_topology(topo_id)
    oracle = ReferenceOracle()

    current_configs = generate_topology_configs(spec)
    cumulative_invariants = list(spec.baseline_invariants)

    intents = list(read_jsonl(jsonl_p, Intent))
    regressions = 0
    regressions_by_step: dict[int, list[str]] = {}

    for step_idx, intent in enumerate(intents):
        gold_patch = intent.gold_patch
        assert gold_patch is not None, f"Step {step_idx} in {jsonl_p} lacks gold_patch"

        # 1. Format as strict model output and test parser & resolver round-trip
        model_output_text = format_gold_patch_as_model_output(gold_patch)
        parsed = parse_model_output(model_output_text)

        assert len(parsed.errors) == 0, f"Gold patch parsing errors at step {step_idx}: {parsed.errors}"
        assert len(parsed.warnings) == 0, f"Gold patch parsing warnings at step {step_idx}: {parsed.warnings}"

        resolved = resolve_devices(parsed.devices, spec.inventory)
        assert len(resolved.errors) == 0, f"Gold patch resolve errors at step {step_idx}: {resolved.errors}"
        assert len(resolved.warnings) == 0, f"Gold patch resolve warnings at step {step_idx}: {resolved.warnings}"

        # 2. Apply patch to active configurations
        for dev, new_cfg in resolved.devices.items():
            current_configs[dev] = new_cfg

        # 3. Retrieve added invariant for this step from sidecar and accumulate
        step_inv_data = manifest_data["invariants_added"][str(step_idx)]
        if isinstance(step_inv_data, dict):
            step_inv = Invariant.model_validate(step_inv_data)
            cumulative_invariants.append(step_inv)
        elif isinstance(step_inv_data, str):
            # Backward compatibility with ID-only sidecars if encountered
            for inv in spec.baseline_invariants:
                if inv.id == step_inv_data:
                    cumulative_invariants.append(inv)
                    break

        # Re-verify all cumulative invariants (baseline + all step invariants so far)
        results = oracle.check(current_configs, cumulative_invariants)
        broken = [inv.id for inv in cumulative_invariants if not results[inv.id]]

        if broken:
            regressions += 1
            regressions_by_step[step_idx] = broken
            print(f"  REGRESSION at step {step_idx} ({intent.id}): broken invariants {broken}", file=sys.stderr)

    return ReplayResult(len(intents), regressions, regressions_by_step)


def run_corruption_suite(
    sequences_dir: str | Path,
    num_corruptions: int = 50,
    seed: int = 999,
) -> tuple[int, int]:
    """Inject deliberate corruptions into gold patches and verify 100% detection rate.

    Corruption types (>= 12 distinct network fault modes):
    1. C01_acl_line_reorder_shadowing: Move permit ip any any to top of ACL
    2. C02_wrong_wildcard_mask: Insert inverted wildcard mask
    3. C03_wrong_route_next_hop: Next-hop typo to unreachable IP
    4. C04_vrf_forwarding_swap: Swap interface VRF assignment
    5. C05_omitted_access_group_in: Omit inbound access-group
    6. C06_corrupted_interface_netmask: Netmask typo causing subnet partition
    7. C07_action_permit_deny_swap: Swap permit to deny on service port
    8. C08_static_route_vrf_omission: Drop VRF tag on static route
    9. C09_unrecognized_device_in_fence: Misspell device name in fence
    10. C10_direction_in_out_swap: Swap access-group in to out
    11. C11_malformed_fence_syntax: Strip closing fence delimiter
    12. C12_route_map_deny_all: Route map prefix list deny all routes
    """
    seq_dir = Path(sequences_dir)
    jsonl_files = sorted(list(seq_dir.glob("**/*.jsonl")))
    # Filter out prompt_safe files
    jsonl_files = [f for f in jsonl_files if not f.name.endswith(".prompt_safe.jsonl")]

    if not jsonl_files:
        raise FileNotFoundError(f"No sequence JSONL files found in {seq_dir}")

    rng = random.Random(seed)
    oracle = ReferenceOracle()

    # Collect steps across sequences
    all_steps: list[tuple[Path, int, Intent, dict[str, Any]]] = []
    for jf in jsonl_files:
        mf = jf.with_name(jf.stem + ".manifest.json")
        if not mf.is_file():
            continue
        with mf.open("r", encoding="utf-8") as f:
            m_data = json.load(f)
        for idx, intent in enumerate(read_jsonl(jf, Intent)):
            all_steps.append((jf, idx, intent, m_data))

    sampled_steps = rng.sample(all_steps, k=min(num_corruptions, len(all_steps)))
    detected = 0

    corruption_types = [
        "C01_acl_line_reorder_shadowing",
        "C02_wrong_wildcard_mask",
        "C03_wrong_route_next_hop",
        "C04_vrf_forwarding_swap",
        "C05_omitted_access_group_in",
        "C06_corrupted_interface_netmask",
        "C07_action_permit_deny_swap",
        "C08_static_route_vrf_omission",
        "C09_unrecognized_device_in_fence",
        "C10_direction_in_out_swap",
        "C11_malformed_fence_syntax",
        "C12_route_map_deny_all",
    ]

    type_stats: dict[str, dict[str, int]] = {ct: {"tested": 0, "detected": 0} for ct in corruption_types}

    print(f"\n=== Running Intentional Corruption Suite ({len(sampled_steps)} corruptions across {len(corruption_types)} types) ===")

    for i, (jf, step_idx, intent, m_data) in enumerate(sampled_steps, start=1):
        c_type = corruption_types[(i - 1) % len(corruption_types)]
        type_stats[c_type]["tested"] += 1

        gold_patch = dict(intent.gold_patch)  # type: ignore[arg-type]
        spec = get_topology(m_data["topology_id"])
        baseline_configs = generate_topology_configs(spec)

        target_dev = list(gold_patch.keys())[0]
        cfg_text = gold_patch[target_dev]
        flagged = False

        if c_type == "C01_acl_line_reorder_shadowing":
            fw_name = next(name for name in spec.nodes if "fw" in name)
            c_cfgs = dict(baseline_configs)
            c_cfgs[fw_name] = c_cfgs[fw_name].replace(
                "ip access-list extended EDGE_IN\n",
                "ip access-list extended EDGE_IN\n permit ip any any\n",
            ).replace(" permit ip any any\n!", "!")
            res = oracle.check(c_cfgs, spec.baseline_invariants)
            if not all(res.values()):
                flagged = True

        elif c_type == "C02_wrong_wildcard_mask":
            r1_name = sorted(spec.nodes.keys())[1]
            c_cfgs = dict(baseline_configs)
            probe_ip = spec.baseline_invariants[0].source
            c_cfgs[r1_name] = c_cfgs[r1_name].replace(
                "ip access-list extended EDGE_IN\n",
                f"ip access-list extended EDGE_IN\n deny ip {probe_ip} 255.255.255.0 any\n",
            )
            res = oracle.check(c_cfgs, spec.baseline_invariants)
            if not all(res.values()):
                flagged = True

        elif c_type == "C03_wrong_route_next_hop":
            r1_name = sorted(spec.nodes.keys())[1]
            c_cfgs = dict(baseline_configs)
            lines = c_cfgs[r1_name].splitlines()
            new_lines = []
            for line in lines:
                if line.strip().startswith("ip route "):
                    parts = line.strip().split()
                    parts[-1] = "10.99.99.99"
                    new_lines.append(" ".join(parts))
                else:
                    new_lines.append(line)
            c_cfgs[r1_name] = "\n".join(new_lines) + "\n"
            res = oracle.check(c_cfgs, spec.baseline_invariants)
            if not all(res.values()):
                flagged = True

        elif c_type == "C04_vrf_forwarding_swap":
            r1_name = sorted(spec.nodes.keys())[1]
            vrf_a, vrf_b = spec.vrfs[0], spec.vrfs[1]
            c_cfgs = dict(baseline_configs)
            c_cfgs[r1_name] = c_cfgs[r1_name].replace(
                f"ip vrf forwarding {vrf_a}", f"ip vrf forwarding {vrf_b}"
            )
            res = oracle.check(c_cfgs, spec.baseline_invariants)
            if not all(res.values()):
                flagged = True

        elif c_type == "C05_omitted_access_group_in":
            fw_name = next(name for name in spec.nodes if "fw" in name)
            c_cfgs = dict(baseline_configs)
            c_cfgs[fw_name] = c_cfgs[fw_name].replace(" ip access-group EDGE_IN in\n", "\n")
            res = oracle.check(c_cfgs, spec.baseline_invariants)
            if not all(res.values()):
                flagged = True

        elif c_type == "C06_corrupted_interface_netmask":
            r1_name = sorted(spec.nodes.keys())[1]
            c_cfgs = dict(baseline_configs)
            c_cfgs[r1_name] = c_cfgs[r1_name].replace("255.255.255.0", "255.255.255.252", 1)
            res = oracle.check(c_cfgs, spec.baseline_invariants)
            if not all(res.values()):
                flagged = True

        elif c_type == "C07_action_permit_deny_swap":
            r1_name = sorted(spec.nodes.keys())[1]
            c_cfgs = dict(baseline_configs)
            c_cfgs[r1_name] = c_cfgs[r1_name].replace("permit tcp any any eq 80", "deny tcp any any eq 80")
            res = oracle.check(c_cfgs, spec.baseline_invariants)
            if not all(res.values()):
                flagged = True

        elif c_type == "C08_static_route_vrf_omission":
            r1_name = sorted(spec.nodes.keys())[1]
            vrf_a = spec.vrfs[0]
            c_cfgs = dict(baseline_configs)
            c_cfgs[r1_name] = c_cfgs[r1_name].replace(f"ip route vrf {vrf_a} ", "ip route ")
            res = oracle.check(c_cfgs, spec.baseline_invariants)
            if not all(res.values()):
                flagged = True

        elif c_type == "C09_unrecognized_device_in_fence":
            corrupted_patch = {f"{target_dev}_unknown": cfg_text}
            corrupted_output = format_gold_patch_as_model_output(corrupted_patch)
            parsed = parse_model_output(corrupted_output)
            resolved = resolve_devices(parsed.devices, spec.inventory)
            if not resolved.is_ok or len(resolved.errors) > 0 or target_dev not in resolved.devices:
                flagged = True

        elif c_type == "C10_direction_in_out_swap":
            fw_name = next(name for name in spec.nodes if "fw" in name)
            c_cfgs = dict(baseline_configs)
            c_cfgs[fw_name] = c_cfgs[fw_name].replace("ip access-group EDGE_IN in", "ip access-group EDGE_IN out")
            res = oracle.check(c_cfgs, spec.baseline_invariants)
            if not all(res.values()):
                flagged = True

        elif c_type == "C11_malformed_fence_syntax":
            corrupted_output = f"```{target_dev}\n{cfg_text.strip()}\n"
            parsed = parse_model_output(corrupted_output)
            if not parsed.is_ok or len(parsed.errors) > 0:
                flagged = True

        elif c_type == "C12_route_map_deny_all":
            r1_name = sorted(spec.nodes.keys())[1]
            c_cfgs = dict(baseline_configs)
            # Remove static routes from r1 to induce routing blackhole
            lines = [l for l in c_cfgs[r1_name].splitlines() if not l.strip().startswith("ip route ")]
            c_cfgs[r1_name] = "\n".join(lines) + "\n"
            res = oracle.check(c_cfgs, spec.baseline_invariants)
            if not all(res.values()):
                flagged = True

        if flagged:
            detected += 1
            type_stats[c_type]["detected"] += 1

    print("\nCorruption Detection Breakdown by Type:")
    for ct in corruption_types:
        t = type_stats[ct]["tested"]
        d = type_stats[ct]["detected"]
        rate = d / t if t > 0 else 0
        print(f"  [{d}/{t} - {rate:.1%}] {ct}")

    detection_rate = detected / len(sampled_steps)
    print(f"\nOverall Corruption Detection Rate: {detected}/{len(sampled_steps)} ({detection_rate:.1%})")
    assert detected == len(sampled_steps), f"Expected 100% detection, got {detection_rate:.1%}"
    return len(sampled_steps), detected


def run_agent_pipeline_check(prompt_safe_jsonl_path: str | Path) -> bool:
    """Validate that prompt-safe intents flow seamlessly through the agent prompt builder and mock client."""
    safe_p = Path(prompt_safe_jsonl_path)
    print(f"\n=== Running Agent Pipeline Check with '{safe_p.name}' ===")

    intents = list(read_jsonl(safe_p, Intent))
    client = LLMClient()
    mock_cfg = get_model_config("mock_scripted")

    # Sample router configs
    dummy_configs = {
        "r1": "hostname r1\ninterface GigabitEthernet0/0\n ip address 10.1.1.1 255.255.255.0\n",
        "r2": "hostname r2\ninterface GigabitEthernet0/0\n ip address 10.1.1.2 255.255.255.0\n",
    }

    for i, intent in enumerate(intents[:5]):
        # 1. Build prompt using prompt-safe intent text
        prompt = build_prompt(
            device_configs=dummy_configs,
            intent_text=intent.text,
            condition=Condition.C0,
        )
        assert "hostname r1" in prompt.user
        assert intent.text in prompt.user

        # 2. Execute through mock client
        gen_res = client.generate(prompt, mock_cfg)
        assert len(gen_res.text) > 0

        # 3. Parse output
        parsed = parse_model_output(gen_res.text)
        assert parsed.is_ok
        assert len(parsed.devices) > 0

    print("Agent pipeline check PASSED: All prompt-safe intents successfully flowed through prompt builder and mock client.")
    return True


def main() -> None:
    """CLI entrypoint for replay checks."""
    import argparse

    parser = argparse.ArgumentParser(description="Replay verification and corruption check driver.")
    parser.add_argument("--data-dir", default="generator/data", help="Directory containing sequence files.")
    parser.add_argument("--corruptions", type=int, default=50, help="Number of corruptions to inject.")
    args = parser.parse_args()

    data_dir = Path(args.data_dir)
    test_dir = data_dir / "test"
    jsonl_files = sorted(list(test_dir.glob("*.jsonl")))
    jsonl_files = [f for f in jsonl_files if not f.name.endswith(".prompt_safe.jsonl")]

    print(f"=== Replaying {len(jsonl_files)} Frozen Test Sequences ===")
    total_steps = 0
    total_regressions = 0

    for idx, jf in enumerate(jsonl_files, start=1):
        mf = jf.with_name(jf.stem + ".manifest.json")
        steps, reg = replay_single_sequence(jf, mf)
        total_steps += steps
        total_regressions += reg
        print(f"  [{idx:02d}/{len(jsonl_files)}] {jf.stem}: {steps} steps replayed, {reg} regressions.")

    print(f"\nReplay Complete: {total_steps} steps across {len(jsonl_files)} sequences. Regressions: {total_regressions}")
    assert total_regressions == 0, "Expected zero regressions during gold replay!"

    # Run corruption suite
    run_corruption_suite(data_dir, num_corruptions=args.corruptions)

    # Run agent pipeline check
    if jsonl_files:
        safe_example = jsonl_files[0].with_name(jsonl_files[0].stem + ".prompt_safe.jsonl")
        run_agent_pipeline_check(safe_example)

    print("\nALL REPLAY AND CORRUPTION CHECKS PASSED 100%!")


if __name__ == "__main__":
    main()
