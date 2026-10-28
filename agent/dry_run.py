"""Dry-run benchmark CLI exercising prompt construction, LLM client, parser, and logging.

Executes a sequence of simulated intent updates against deterministic mock models
without requiring network access, external services, or API keys. Verifies that
all contract serialization and error-handling pipelines function end-to-end.
"""

import argparse
from pathlib import Path
import sys
import uuid

from agent.contract.models import Condition, Intent, IntentType, StepOutcome, StepResult
from agent.llm.client import LLMClient
from agent.llm.config import get_model_config
from agent.logging_.step_log import StepLogger, get_completed_steps
from agent.prompts.builder import build_prompt
from agent.prompts.parser import parse_model_output


def generate_fake_intents(count: int, sequence_id: str = "seq-dryrun-001") -> list[Intent]:
    """Generate deterministic simulated intents for dry-run verification."""
    templates = [
        (
            IntentType.ACL_PERMIT,
            "Permit TCP port 80 (HTTP) traffic from subnet 10.0.1.0/24 to web server 10.0.2.10/32 on router r1.",
            {"proto": "tcp", "dst_port": 80, "src": "10.0.1.0/24", "dst": "10.0.2.10/32"},
        ),
        (
            IntentType.ACL_PERMIT,
            "Permit TCP port 443 (HTTPS) traffic from subnet 10.0.1.0/24 to web server 10.0.2.10/32 on router r1.",
            {"proto": "tcp", "dst_port": 443, "src": "10.0.1.0/24", "dst": "10.0.2.10/32"},
        ),
        (
            IntentType.ACL_DENY,
            "Deny Telnet (TCP port 23) from any source to management network 10.0.2.0/24 across all border devices.",
            {"proto": "tcp", "dst_port": 23, "src": "any", "dst": "10.0.2.0/24"},
        ),
        (
            IntentType.PREFIX_FILTER,
            "Filter inbound BGP prefix announcement 192.168.100.0/24 on peer router r2.",
            {"prefix": "192.168.100.0/24", "action": "deny"},
        ),
        (
            IntentType.VRF_ISOLATE,
            "Enforce strict VRF isolation between guest-wifi and internal-production zones on router r1.",
            {"vrf_a": "guest-wifi", "vrf_b": "internal-prod"},
        ),
    ]

    intents: list[Intent] = []
    for step_idx in range(count):
        intent_type, text, params = templates[step_idx % len(templates)]
        intents.append(
            Intent(
                id=f"dryrun-intent-{step_idx:03d}",
                sequence_id=sequence_id,
                step=step_idx,
                type=intent_type,
                params=params,
                text=text,
                adds_invariant=f"dryrun-inv-{step_idx:03d}",
            )
        )
    return intents


def get_initial_configs() -> dict[str, str]:
    """Return initial baseline router configurations for dry run."""
    return {
        "r1": (
            "hostname r1\n"
            "!\n"
            "interface GigabitEthernet0/0\n"
            " description LAN interface\n"
            " ip address 10.0.1.1 255.255.255.0\n"
            "!\n"
            "interface GigabitEthernet0/1\n"
            " description Uplink to r2\n"
            " ip address 172.16.0.1 255.255.255.252\n"
            "!\n"
            "ip access-list extended BASE_ACL\n"
            " permit icmp any any\n"
        ),
        "r2": (
            "hostname r2\n"
            "!\n"
            "interface GigabitEthernet0/0\n"
            " description Server farm\n"
            " ip address 10.0.2.1 255.255.255.0\n"
            "!\n"
            "interface GigabitEthernet0/1\n"
            " description Uplink to r1\n"
            " ip address 172.16.0.2 255.255.255.252\n"
            "!\n"
            "router bgp 65000\n"
            " neighbor 172.16.0.1 remote-as 65000\n"
        ),
    }


def run_dry_run(
    model_name: str = "mock",
    steps: int = 5,
    config_path: str = "configs/models.yaml",
    output_path: str = "runs/dry_run.jsonl",
    condition: str = "C0",
    sequence_id: str = "seq-dryrun-001",
) -> int:
    """Execute dry-run pipeline and print summary table.

    Returns:
        0 on success, non-zero on failure.
    """
    print(f"=== Starting Dry Run ===")
    print(f"Model: {model_name} | Steps: {steps} | Condition: {condition} | Output: {output_path}")

    try:
        cfg = get_model_config(model_name, config_path)
    except Exception as err:
        print(f"Error loading configuration: {err}", file=sys.stderr)
        return 1

    client = LLMClient()
    logger = StepLogger(output_path)
    completed_steps = get_completed_steps(output_path)

    run_id = f"dryrun-{uuid.uuid4().hex[:8]}"
    intents = generate_fake_intents(steps, sequence_id=sequence_id)
    device_configs = get_initial_configs()

    results: list[StepResult] = []

    for intent in intents:
        step_key = (run_id, intent.sequence_id, intent.step)
        if step_key in completed_steps:
            print(f"Step {intent.step} already completed in {output_path}; skipping.")
            continue

        prompt = build_prompt(
            device_configs=device_configs,
            intent_text=intent.text,
            condition=condition,
        )

        gen_res = client.generate(prompt, cfg)
        parsed = parse_model_output(gen_res.text)

        if parsed.is_ok:
            outcome = StepOutcome.SUCCESS
            parse_ok = True
            new_intent_ok = True
            committed = True
            # Update local topology state with committed device configs
            for dev, new_conf in parsed.devices.items():
                device_configs[dev] = new_conf
        else:
            outcome = StepOutcome.PARSE_FAIL
            parse_ok = False
            new_intent_ok = False
            committed = False

        step_res = StepResult(
            run_id=run_id,
            model=cfg.name,
            condition=Condition(condition),
            sequence_id=intent.sequence_id,
            step=intent.step,
            parse_ok=parse_ok,
            new_intent_ok=new_intent_ok,
            broken_invariants=[],
            outcome=outcome,
            retries=0,
            tokens_in=gen_res.tokens_in,
            tokens_out=gen_res.tokens_out,
            tokens_estimated=gen_res.tokens_estimated,
            latency_ms=gen_res.latency_ms,
            committed=committed,
        )

        logger.log(step_res)
        results.append(step_res)

    # Print summary table
    print("\n" + "=" * 80)
    print(f"{'Step':<6} | {'Intent ID':<18} | {'Parse OK':<9} | {'Outcome':<12} | {'Tokens (In/Out)':<16} | {'Latency':<8}")
    print("-" * 80)
    for res in results:
        token_str = f"{res.tokens_in} / {res.tokens_out}"
        latency_str = f"{res.latency_ms} ms"
        parse_str = "YES" if res.parse_ok else "NO"
        print(f"{res.step:<6} | {f'intent-{res.step:03d}':<18} | {parse_str:<9} | {res.outcome.value:<12} | {token_str:<16} | {latency_str:<8}")
    print("=" * 80)

    success_count = sum(1 for r in results if r.outcome == StepOutcome.SUCCESS)
    print(f"\nDry run complete: {len(results)} steps logged to '{output_path}'.")
    print(f"Summary: {success_count}/{len(results)} steps succeeded.\n")
    return 0


def main() -> None:
    """CLI entry point for python -m agent.dry_run."""
    parser = argparse.ArgumentParser(
        description="Dry-run runner for LLM network policy regression benchmark."
    )
    parser.add_argument(
        "--model",
        default="mock",
        help="Model configuration identifier from configs/models.yaml (default: mock).",
    )
    parser.add_argument(
        "--steps",
        type=int,
        default=5,
        help="Number of intent steps to simulate (default: 5).",
    )
    parser.add_argument(
        "--config",
        default="configs/models.yaml",
        help="Path to models.yaml configuration file (default: configs/models.yaml).",
    )
    parser.add_argument(
        "--output",
        default="runs/dry_run.jsonl",
        help="Path to JSONL run log destination (default: runs/dry_run.jsonl).",
    )
    parser.add_argument(
        "--condition",
        default="C0",
        choices=["C0", "C1", "C2", "C3"],
        help="Experimental context condition (default: C0).",
    )

    args = parser.parse_args()
    code = run_dry_run(
        model_name=args.model,
        steps=args.steps,
        config_path=args.config,
        output_path=args.output,
        condition=args.condition,
    )
    sys.exit(code)


if __name__ == "__main__":
    main()
