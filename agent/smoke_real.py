"""Real-model smoke test CLI exercising end-to-end inference, parsing, resolution, and logging.

Executes a small set of configuration intents against either a real LLM endpoint
(hosted OpenAI-compatible, local Ollama, vLLM) or a mock model.
Validates model output syntax, resolves device names against the active inventory,
logs results to JSONL, and renders a summary table without logging credentials.
"""

import argparse
import os
from pathlib import Path
import sys
import uuid

from agent.contract.models import Condition, StepOutcome, StepResult
from agent.llm.client import LLMClient
from agent.llm.config import ProviderType, get_model_config
from agent.logging_.step_log import StepLogger
from agent.prompts.builder import build_prompt
from agent.prompts.parser import parse_model_output
from agent.prompts.resolve import resolve_devices


EMBEDDED_ROUTER_CONFIGS: dict[str, str] = {
    "r1": (
        "hostname r1\n"
        "!\n"
        "interface GigabitEthernet0/0\n"
        " description WAN Uplink to ISP\n"
        " ip address 203.0.113.1 255.255.255.0\n"
        "!\n"
        "interface GigabitEthernet0/1\n"
        " description Internal Link to r2\n"
        " ip address 10.1.12.1 255.255.255.252\n"
        "!\n"
        "ip access-list extended EDGE_IN\n"
        " permit icmp any any\n"
    ),
    "r2": (
        "hostname r2\n"
        "!\n"
        "interface GigabitEthernet0/0\n"
        " description Core Link to r1\n"
        " ip address 10.1.12.2 255.255.255.252\n"
        "!\n"
        "interface GigabitEthernet0/1\n"
        " description Distribution Link to r3\n"
        " ip address 10.1.23.1 255.255.255.252\n"
    ),
    "r3": (
        "hostname r3\n"
        "!\n"
        "interface GigabitEthernet0/0\n"
        " description Core Link to r2\n"
        " ip address 10.1.23.2 255.255.255.252\n"
        "!\n"
        "interface GigabitEthernet0/1\n"
        " description Server Subnet\n"
        " ip address 10.2.0.1 255.255.255.0\n"
    ),
}

EMBEDDED_INTENTS: list[str] = [
    (
        "On router r1, add a rule to access-list EDGE_IN permitting TCP port 443 (HTTPS) "
        "traffic from any source to host 10.2.0.10."
    ),
    (
        "On router r1, add a rule to access-list EDGE_IN denying TCP port 23 (Telnet) "
        "from any source to any destination."
    ),
    (
        "On router r2, change the description of interface GigabitEthernet0/1 "
        "to 'Core-to-Access trunk to r3'."
    ),
    (
        "On router r3, add an extended access-list named SERVER_FILTER permitting "
        "UDP port 53 (DNS) traffic from 10.2.0.0/24 to any."
    ),
    (
        "On router r3, add an IP route for prefix 0.0.0.0 0.0.0.0 next-hop 10.1.23.1."
    ),
]


def run_smoke_test(
    model_name: str,
    n_steps: int = 5,
    config_path: str = "configs/models.yaml",
    output_path: str = "runs/smoke_real.jsonl",
    raw_output_path: str = "runs/smoke_real_raw.txt",
) -> int:
    """Run smoke test against the specified model target."""
    print("=== Starting Real-Model Smoke Test ===")
    print(f"Model: {model_name} | Max Intents: {n_steps} | Output: {output_path}")

    try:
        cfg = get_model_config(model_name, config_path)
    except Exception as err:
        print(f"Configuration error: {err}", file=sys.stderr)
        return 1

    # Security check: fail fast if the requested env var is missing or empty
    if cfg.provider == ProviderType.OPENAI_COMPATIBLE and cfg.api_key_env:
        key = os.environ.get(cfg.api_key_env)
        if not key or not key.strip():
            print(
                f"\nERROR: Environment variable '{cfg.api_key_env}' required for model '{cfg.name}' "
                f"is not set or empty.\nPlease set this environment variable before running smoke tests.",
                file=sys.stderr,
            )
            return 1

    client = LLMClient()
    logger = StepLogger(output_path)
    run_id = f"smoke-{uuid.uuid4().hex[:8]}"

    # Initialize / overwrite the raw responses output file
    raw_path = Path(raw_output_path)
    raw_path.parent.mkdir(parents=True, exist_ok=True)
    with raw_path.open("w", encoding="utf-8") as f_raw:
        pass

    # Copy current router configs to allow sequential mutation
    current_configs = dict(EMBEDDED_ROUTER_CONFIGS)
    inventory = list(EMBEDDED_ROUTER_CONFIGS.keys())
    intents_to_run = EMBEDDED_INTENTS[: max(1, min(n_steps, len(EMBEDDED_INTENTS)))]

    table_rows: list[dict[str, object]] = []

    for step_idx, intent_text in enumerate(intents_to_run):
        prompt = build_prompt(
            device_configs=current_configs,
            intent_text=intent_text,
            condition=Condition.C0,
        )

        try:
            gen_res = client.generate(prompt, cfg)
        except Exception as err:
            print(f"Inference error on step {step_idx}: {err}", file=sys.stderr)
            return 1

        # Record raw model response with intent ID and separator line
        intent_id = f"intent_{step_idx}"
        with raw_path.open("a", encoding="utf-8") as f_raw:
            f_raw.write("================================================================================\n")
            f_raw.write(f"Intent ID: {intent_id}\n")
            f_raw.write("================================================================================\n")
            f_raw.write(gen_res.text)
            if not gen_res.text.endswith("\n"):
                f_raw.write("\n")
            f_raw.write("\n")

        parsed = parse_model_output(gen_res.text)
        resolved = resolve_devices(parsed.devices, inventory)

        all_errors = parsed.errors + resolved.errors
        all_warnings = parsed.warnings + resolved.warnings
        parse_ok = (len(all_errors) == 0) and (len(resolved.devices) > 0 or len(parsed.devices) == 0)

        if parse_ok and resolved.devices:
            # Update local configs with committed changes
            for dev_name, new_cfg in resolved.devices.items():
                current_configs[dev_name] = new_cfg
            outcome = StepOutcome.SUCCESS
            committed = True
        elif not parse_ok:
            outcome = StepOutcome.PARSE_FAIL
            committed = False
        else:
            outcome = StepOutcome.SUCCESS
            committed = False

        step_res = StepResult(
            run_id=run_id,
            model=cfg.name,
            condition=Condition.C0,
            sequence_id="smoke-seq-001",
            step=step_idx,
            parse_ok=parse_ok,
            new_intent_ok=parse_ok,
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

        table_rows.append(
            {
                "step": step_idx,
                "parse_ok": "YES" if parse_ok else "NO",
                "errors_count": len(all_errors),
                "warnings_count": len(all_warnings),
                "tokens": f"{gen_res.tokens_in} / {gen_res.tokens_out}",
                "estimated": "YES" if gen_res.tokens_estimated else "NO",
                "latency": f"{gen_res.latency_ms} ms",
            }
        )

    # Print summary table
    print("\n" + "=" * 88)
    print(
        f"{'Step':<6} | {'Parse OK':<9} | {'Errors':<8} | {'Warnings':<9} | "
        f"{'Tokens (In/Out)':<16} | {'Estimated':<10} | {'Latency':<8}"
    )
    print("-" * 88)
    for r in table_rows:
        print(
            f"{r['step']:<6} | {r['parse_ok']:<9} | {r['errors_count']:<8} | "
            f"{r['warnings_count']:<9} | {r['tokens']:<16} | {r['estimated']:<10} | {r['latency']:<8}"
        )
    print("=" * 88)
    print(f"\nSmoke test complete: {len(table_rows)} steps logged to '{output_path}'.\n")
    return 0


def main() -> None:
    """CLI entry point for python -m agent.smoke_real."""
    parser = argparse.ArgumentParser(
        description="Real-model smoke test for LLM network policy regression benchmark."
    )
    parser.add_argument(
        "--model",
        required=True,
        help="Target model configuration profile name from configs/models.yaml.",
    )
    parser.add_argument(
        "--n",
        type=int,
        default=5,
        help="Number of intent steps to run (1 to 5, default: 5).",
    )
    parser.add_argument(
        "--config",
        default="configs/models.yaml",
        help="Path to models.yaml (default: configs/models.yaml).",
    )
    parser.add_argument(
        "--output",
        default="runs/smoke_real.jsonl",
        help="Path to output JSONL log (default: runs/smoke_real.jsonl).",
    )
    parser.add_argument(
        "--raw-output",
        default="runs/smoke_real_raw.txt",
        help="Path to raw output text file (default: runs/smoke_real_raw.txt).",
    )

    args = parser.parse_args()
    code = run_smoke_test(
        model_name=args.model,
        n_steps=args.n,
        config_path=args.config,
        output_path=args.output,
        raw_output_path=args.raw_output,
    )
    sys.exit(code)


if __name__ == "__main__":
    main()
