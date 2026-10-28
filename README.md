# Network Policy LLM Regression Benchmark: Agent Harness

The agent side of a benchmark framework measuring how quickly Large Language Models (LLMs) regress or break earlier network policies (reachability, isolation, and waypoint routing) when applying sequential router and firewall configuration changes.

This repository provides:
1. **Shared Data Contract**: Strongly typed Pydantic models with `extra="forbid"`, line-number-aware JSONL validation, and cross-team JSON Schema exports.
2. **Model Wrapper**: Unified inference client supporting deterministic offline mocks (`echo`, `scripted_edit`, `malformed`) and OpenAI-compatible endpoints (hosted APIs, local Ollama, vLLM) with exponential backoff and jitter.
3. **Prompt Builder & Robust Parser**: Versioned system prompt, condition-aware builder (C0–C3), inventory-aware device resolver, and fault-tolerant output parser tolerating messy outputs without crashing.
4. **Crash-Safe Logging**: Append-only JSONL logger with immediate flushing and resumption indexing to skip completed steps.
5. **Dry Run & Smoke CLI**: Offline verification demo and real-model smoke test driver exercising the complete pipeline end-to-end.

---

## Repository Layout

```text
agent/
  contract/
    models.py             # Intent, Invariant, StepResult Pydantic models (extra='forbid')
    io.py                 # Line-validated JSONL read/append and schema exporters
    schemas/              # Exported JSON Schemas for cross-language validation
  llm/
    config.py             # YAML model configuration parser (ModelConfig)
    client.py             # GenerationResult, MockAdapter, OpenAICompatibleAdapter
  prompts/
    templates.py          # Versioned system prompt (PROMPT_VERSION = 'v1')
    builder.py            # build_prompt() for C0, C1, C2, C3 conditions
    parser.py             # parse_model_output() with fault-tolerant fence extraction
    resolve.py            # resolve_devices() matching raw names against inventory
  logging_/
    step_log.py           # Crash-safe StepLogger and get_completed_steps() resume helper
  dry_run.py              # Dry-run CLI driver
  smoke_real.py           # Real-model smoke test CLI driver
configs/
  models.yaml             # Model definitions and sampling configurations
tests/
  test_contract.py        # Schema validation, extra-field rejection, JSONL line reporting
  test_llm.py             # Mock adapters, backoff retry logic, token estimation
  test_prompts.py         # Prompt builder, structural gold_patch protection, messy parser cases
  test_resolve.py         # Inventory-aware device name resolution and error handling
  test_logging.py         # Crash-safe step logging, corruption handling, resume indexer
  test_dry_run.py         # End-to-end dry run execution and summary table verification
dry_run.py                # Root forwarding entrypoint
README.md                 # System documentation and decisions
```

---

## How to Run Tests

> [!IMPORTANT]
> **Run `pytest` from the project root directory.** Running `pytest` from inside the `agent/` folder will find 0 tests because all test suites reside in the root `tests/` directory.

All tests run completely offline with no network access, external services, or API credentials required:

```bash
# Run the complete test suite (from project root)
python -m pytest -v

# Run tests for a specific module
python -m pytest tests/test_contract.py -v
python -m pytest tests/test_llm.py -v
python -m pytest tests/test_prompts.py -v
python -m pytest tests/test_resolve.py -v
python -m pytest tests/test_logging.py -v
python -m pytest tests/test_dry_run.py -v
```

---

## How to Run the Dry Run

The dry run simulates multi-step configuration updates, invokes the mock adapter, parses outputs, logs `StepResult` records to JSONL, and prints a terminal summary table.

### 1. Default Echo Mock (5 steps, condition C0)
```bash
python -m agent.dry_run --model mock --steps 5
```
Or using the root script:
```bash
python dry_run.py --model mock --steps 5
```

### 2. Scripted Edit Mock (Simulates progressive configuration changes)
```bash
python -m agent.dry_run --model mock_scripted --steps 3 --output runs/dry_run_scripted.jsonl
```

### 3. Malformed Mock (Demonstrates automated `PARSE_FAIL` outcome classification)
```bash
python -m agent.dry_run --model mock_malformed --steps 2 --output runs/dry_run_malformed.jsonl
```

### 4. Custom Output and Condition
```bash
python -m agent.dry_run --model mock --steps 10 --condition C3 --output runs/experiment_c3.jsonl
```

---

## Smoke test with a real model

The real-model smoke test CLI (`agent.smoke_real`) validates end-to-end execution against a real LLM endpoint (hosted OpenAI-compatible APIs, local Ollama, or vLLM). It exercises prompt building, inference, code fence parsing, inventory device-name resolution, JSONL step logging, and summary reporting.

### Running the Smoke Test

Execute the smoke test from the project root:
```bash
python -m agent.smoke_real --model NAME --n 5
```
CLI arguments:
- `--model NAME`: Profile name defined under `models` in `configs/models.yaml` (required).
- `--n 5`: Number of intent steps to run from embedded scenarios (1 to 5, default: 5).
- `--config configs/models.yaml`: Path to models YAML configuration file (default: `configs/models.yaml`).
- `--output runs/smoke_real.jsonl`: Destination JSONL log file (default: `runs/smoke_real.jsonl`).

### Configuring Models in `configs/models.yaml`

To test against an endpoint, add a model entry under the `models` key in `configs/models.yaml`.

#### 1. Hosted OpenAI-Compatible Entry
```yaml
models:
  openai_gpt4o_mini:
    name: openai_gpt4o_mini
    provider: openai_compatible
    base_url: https://api.openai.com/v1
    api_key_env: OPENAI_API_KEY      # Environment variable name only
    model_id: gpt-4o-mini
    temperature: 0.0
    max_tokens: 4096
    seed: 42
    timeout_s: 60.0
```

#### 2. Local Ollama Entry
Local Ollama endpoints do not require authentication:
```yaml
models:
  local_ollama:
    name: local_ollama
    provider: openai_compatible
    base_url: http://localhost:11434/v1
    model_id: llama3.1:8b
    temperature: 0.0
    max_tokens: 4096
    timeout_s: 120.0
```

> [!NOTE]
> **API Keys Come From Environment Variables Only**:
> The `api_key_env` field specifies the **name** of the environment variable (e.g. `OPENAI_API_KEY`), never the key itself. API keys must never be hardcoded into YAML files or source code. If `api_key_env` is set but the environment variable is missing or empty, the harness fails fast before making any HTTP requests.
> - **PowerShell (Windows)**: `$env:OPENAI_API_KEY="sk-..."`
> - **Bash (Linux/macOS)**: `export OPENAI_API_KEY="sk-..."`

### What the Output Table Means

Upon completion, `agent.smoke_real` renders an ASCII summary table in the terminal:
```text
========================================================================================
Step   | Parse OK  | Errors   | Warnings  | Tokens (In/Out)  | Estimated  | Latency 
----------------------------------------------------------------------------------------
0      | YES       | 0        | 0         | 185 / 72         | NO         | 1240 ms
1      | YES       | 0        | 1         | 210 / 85         | NO         | 1120 ms
========================================================================================
```
The columns represent:
- **`Step`**: Zero-based sequence step index.
- **`Parse OK`**: Whether the model output successfully parsed into valid configuration blocks and all target devices resolved against the topology inventory (`YES` / `NO`).
- **`Errors`**: Count of fatal parsing errors (unclosed fences, missing device names, empty blocks) and inventory resolution errors (unrecognized or ambiguous device names).
- **`Warnings`**: Count of non-fatal warnings (e.g. conversational prose outside fences ignored, or whitespace and casing normalized to canonical inventory names).
- **`Tokens (In/Out)`**: Prompt input tokens and completion output tokens consumed during the step.
- **`Estimated`**: `YES` if token counts were estimated using the `len(text) // 4` heuristic because the provider returned no usage data; `NO` if exact usage statistics were returned by the API.
- **`Latency`**: Elapsed wall-clock inference latency in milliseconds.

---

## How to Add a Model

Add a new entry under the `models` key in `configs/models.yaml`.

### 1. Hosted OpenAI-compatible Endpoint (e.g. OpenAI, DeepSeek, Together, Groq)
```yaml
models:
  my_hosted_model:
    name: my_hosted_model
    provider: openai_compatible
    base_url: https://api.openai.com/v1
    api_key_env: OPENAI_API_KEY      # Name of environment variable only (never hardcode keys)
    model_id: gpt-4o-mini
    temperature: 0.0
    max_tokens: 4096
    seed: 42
    timeout_s: 60.0
```
Before running, set the environment variable:
- **Windows (PowerShell)**: `$env:OPENAI_API_KEY="sk-..."`
- **Linux/macOS**: `export OPENAI_API_KEY="sk-..."`

### 2. Local Server (Ollama)
Local Ollama endpoints do not require authentication:
```yaml
models:
  local_llama3:
    name: local_llama3
    provider: openai_compatible
    base_url: http://localhost:11434/v1
    model_id: llama3.1:8b
    temperature: 0.0
    max_tokens: 4096
    timeout_s: 120.0
```

### 3. Local Server (vLLM)
```yaml
models:
  local_vllm:
    name: local_vllm
    provider: openai_compatible
    base_url: http://localhost:8000/v1
    api_key_env: VLLM_API_KEY        # Optional; omit if server launched without --api-key
    model_id: meta-llama/Llama-3.1-8B-Instruct
    temperature: 0.0
    max_tokens: 4096
    timeout_s: 120.0
```

---

## How to Change the Prompt

1. **System Prompt & Format Instructions** (`agent/prompts/templates.py`):
   - Modify `SYSTEM_PROMPT`.
   - When modifying instructions or changing output constraints, bump `PROMPT_VERSION` (e.g., from `"v1"` to `"v2"`) to ensure benchmark run comparability is tracked.
2. **Context & Section Assembly** (`agent/prompts/builder.py`):
   - The `build_prompt` function assembles the prompt according to experimental conditions:
     - `C0`: Baseline (device configs + intent text).
     - `C1` / `C2`: Verification feedback section appended.
     - `C3`: Policy ledger section included (invariants that must continue to hold).
   - **Safety Invariant**: Prompts must **never** include ground truth patches (`gold_patch`) or internal verification data. Gold patches are protected structurally: `build_prompt` rejects objects carrying `gold_patch`, and there is deliberately no keyword filter on the word `gold` (allowing valid networking terms such as `gold-vlan`).
3. **Output Parser** (`agent/prompts/parser.py`):
   - If output format requirements evolve, adjust `parse_model_output`.
   - The parser extracts device configuration code blocks in the form:
     ````text
     ```<device_name>
     <complete configuration text>
     ```
     ````
   - Handles Windows line endings, unclosed fences, empty blocks, duplicate blocks (last wins with error recorded), and conversational prose (ignored with warning).
4. **Device Name Resolution** (`agent/prompts/resolve.py`):
   - Handles mapping model-emitted device names to canonical topology inventory devices (see Decision 9 for architecture details).

---

## Contract changes

The shared data contract defines the strict Pydantic models (`Intent`, `Invariant`, `StepResult`) shared across the sequence generator, LLM agent harness, and Batfish verification oracle.

Changes made to the shared data contract since the first version:
- **`StepResult.tokens_estimated` field added**: Added an optional boolean field `tokens_estimated: bool = False` to `StepResult` (in `agent/contract/models.py`). This indicates whether token counts were estimated using the `len(text) // 4` fallback heuristic when a provider (such as local Ollama or certain vLLM setups) returns no token usage statistics in its response payload.
- **JSON Schemas regenerated**: The shared JSON Schemas in `agent/contract/schemas/` (and mirrored to `contract/schemas/`) were regenerated using `export_schemas()` so that `step_result.schema.json` defines the `tokens_estimated` property (`"type": "boolean"`, `"default": false`).

> [!WARNING]
> **Teammates must approve contract changes**:
> Because the data contract is shared across independently developed components (sequence generator, agent harness, Batfish oracle), any modifications to contract models or exported JSON schemas require explicit teammate review and approval before merging to prevent breaking downstream pipelines.

---

## Decisions

To adhere to the minimal-complexity constraint and prevent scope creep, the following design decisions were made:

1. **Strict Data Contract (`extra="forbid"`)**:
   - All shared contract models (`Intent`, `Invariant`, `StepResult`, `ModelConfig`) use Pydantic v2 `extra="forbid"`. This prevents silent field mismatches between sequence generator outputs, Batfish verification, and agent logging.
2. **Canonical Schemas and Schema Export Mirroring**:
   - `agent/contract/schemas/` is the canonical schema directory. In addition, `export_schemas()` mirrors these schemas to root `contract/schemas/` so team members developing the Batfish oracle or generator can validate their files regardless of their working directory. Both schema directories are verified to be byte-for-byte identical.
3. **No Key Leakage & Missing Key Fail-Fast**:
   - `ModelConfig.api_key_env` stores the **name** of the environment variable (e.g. `OPENAI_API_KEY`). The adapter never logs or prints the key value. If the specified environment variable is missing or empty, `OpenAICompatibleAdapter` immediately raises a clear `ValueError`.
4. **Token Estimation Fallback**:
   - For endpoints (like some local Ollama or vLLM setups) that omit token usage statistics in the response payload, tokens are estimated as `max(1, len(text) // 4)` and the `estimated_tokens: bool` flag is set to `True` on the `GenerationResult`.
5. **Crash-Safe Append-Only JSONL Logging**:
   - `StepLogger` formats the complete serialized JSON line in memory first, performs a single `write()`, and immediately executes `f.flush()` and `os.fsync(f.fileno())`. This prevents half-written or torn lines during sudden process termination.
6. **Resumption Skip Logic**:
   - `get_completed_steps()` identifies already finished `(run_id, sequence_id, step)` tuples from the output file. Corrupted or partial trailing lines from interrupted runs are automatically ignored so interrupted benchmark runs can resume without manual log cleanup.
7. **Parser Fault-Tolerance**:
   - `parse_model_output` never raises an exception. Malformed outputs (unclosed fences, empty blocks, missing blocks) return a `ParsedOutput` with diagnostic strings in `errors`. The benchmark driver uses `errors` to classify the step as `PARSE_FAIL`.
8. **Stateless Retries Across Steps**:
   - Retries are stateless across steps: a prompt contains only current device configs, this step's intent text, the condition, and optionally this step's most recent feedback, this step's previous attempt, and the ledger. No history from earlier steps is ever included.
9. **Device-Name Resolution Outside the Parser**:
   - Device-name resolution lives outside the parser: `parse_model_output` preserves exact names; `resolve_devices` matches case-insensitively with trimmed whitespace against the topology inventory, warns on each fix, errors on unknown or ambiguous names, and never creates devices.
10. **Structural Protection for Gold Patches**:
    - Gold patches are protected structurally: `build_prompt` rejects objects carrying `gold_patch`, and there is deliberately no keyword filter on the word gold.
11. **Token Counts and Estimation Flag**:
    - Token counts: when a provider returns no usage data, tokens are estimated as len(text)/4 and flagged with tokens_estimated.
