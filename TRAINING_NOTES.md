# Training Notes: SFT & QLoRA Fine-Tuning Recipe

> [!WARNING]
> **EXPLICIT DIRECTIVE: DO NOT RUN THIS TRAINING SCRIPT NOW.**
> These notes and the reference QLoRA script below are specified exclusively for post-baseline fine-tuning investigations once baseline experimental conditions (C0–C3) are fully established.

---

## 1. Dataset Dimensions & SFT Export

The instruction-tuning dataset exported by `agent.training.export_sft` has the following characteristics:
- **Total Sequences**: 10 sequences from `generator/data/train/`.
  - 5 sequences on `topo_train_medium` (6 routers: `tm_r1`–`tm_r6`).
  - 5 sequences on `topo_train_small` (3 routers: `tr_r1`–`tr_r3`).
- **Total Steps / Examples**: 500 examples (50 steps per sequence).
- **Prompt Volume**: ~2,918,608 characters (~730,000 tokens at ~4 chars/token).
- **Target Patch Volume**: ~250,956 characters (~62,700 tokens).
- **Leakage Status**: Strict zero-leakage verified against all 44 test sequence IDs, test topologies (`topo_test_large`, `topo_test_small`), and reserved test paraphrase n-grams (`TEST_RESERVED_PHRASES`).
- **Format**: JSONL records containing `prompt`, `response`, and standard `messages` array (`[system, user, assistant]`).

---

## 2. Why 10 Sequences (500 Steps) Is Probably Too Few

While 500 examples are sufficient to verify that the fine-tuning pipeline executes and that the model learns the syntax of Cisco-style configuration blocks, 10 sequences represent a sparse training sample for long-horizon policy coherence:

1. **Topological Overfitting**:
   - The training data encompasses only two topologies (`topo_train_small` and `topo_train_medium`).
   - The model can easily memorize static artifact properties: specific router names (`tr_r1`–`tr_r3`, `tm_r1`–`tm_r6`), assigned IP subnets (`10.10.x.x`, `10.11.x.x`), and VRFs (`RED`, `BLUE`). When tested on `topo_test_large` (8 routers) or unseen test subnets, models trained on small corpora frequently suffer out-of-distribution failure or invent non-existent interface names.

2. **Combinatorial Sparsity of Invariants**:
   - Sequential configuration degradation occurs when an edit subtly shadows, masks, or overrides a rule introduced dozens of steps earlier.
   - With only 500 steps across 5 intent types (`acl_permit`, `acl_deny`, `vrf_isolate`, `prefix_filter`, `waypoint`), the model encounters only a tiny fraction of the interaction space between overlapping ACL masks, BGP route maps, and inter-VRF routing leaks.

3. **Absence of Negative and Recovery Examples (Exposure Bias)**:
   - SFT on gold patches trains the model *only* on the forward-optimal transition from state $S_t$ to $S_{t+1}$ assuming $S_t$ was perfectly configured.
   - It provides zero training signal on how to recover when a previous step drifted or how to perform minimal-impact surgical edits under constraint conflicts.

4. **Recommended Scale**:
   - For robust generalization to 200-step sequences, the training corpus should be expanded to **50–100 sequences (2,500–5,000 steps)** spanning at least 6–8 distinct training topologies with varied ASN schemes, interface naming patterns, and synthetic repair rollbacks.

---

## 3. QLoRA Recipe for Free Google Colab T4 (15–16 GB VRAM)

The Google Colab free tier provides an NVIDIA Tesla T4 (15,360 MiB VRAM). Full fine-tuning of an 8B parameter model is impossible on this GPU, but 4-bit Quantized Low-Rank Adaptation (QLoRA) fits comfortably within the budget.

### 3.1 Model Choice
- **Recommended**: `meta-llama/Meta-Llama-3.1-8B-Instruct` or `Qwen/Qwen2.5-7B-Instruct`.
- **Reason**: Excellent native support for instruction following, 8k+ context length, strong base reasoning on code and structured network configuration syntax.

### 3.2 Hyperparameter & Quantization Specification
| Parameter | Value | Justification |
| :--- | :--- | :--- |
| **Quantization** | 4-bit NormalFloat (NF4) | `bitsandbytes` NF4 preserves base weight precision near FP16 levels while shrinking weight memory by 75%. |
| **Compute Dtype** | `torch.float16` | Native FP16 tensor core acceleration on T4 architecture. |
| **Double Quantization** | `True` | Compresses quantization constants, saving ~0.4 bits/param (~400 MB VRAM). |
| **LoRA Rank ($r$)** | `16` | Sufficient representation capacity for configuration syntax without overfitting. |
| **LoRA Alpha ($lpha$)** | `32` | Standard $2 	imes r$ scaling factor for adapter weight initialization. |
| **LoRA Dropout** | `0.05` | Regularization against memorizing training router names. |
| **Target Modules** | All linear layers | `["q_proj", "k_proj", "v_proj", "o_proj", "gate_proj", "up_proj", "down_proj"]` |
| **Per-Device Batch Size** | `1` | Avoids out-of-memory during long configuration context processing. |
| **Gradient Accumulation** | `16` | Delivers an effective batch size of 16 for stable gradient updates. |
| **Learning Rate** | `2e-4` | Optimal empirical learning rate for QLoRA adapters with cosine decay. |
| **Warmup Ratio** | `0.03` | Smooth adaptation during initial steps. |
| **Epochs** | `3` | Prevents degradation on small 500-sample dataset. |
| **Max Sequence Length** | `2048` | Covers system prompt, router inventory, current configs, and patch. |
| **Optimizer** | `paged_adamw_8bit` | Offloads optimizer states dynamically to CPU memory when VRAM spikes. |
| **Gradient Checkpointing** | `True` | Trades ~20% compute time to reduce activation memory by over 60%. |

### 3.3 T4 VRAM Budget Breakdown
```text
+-----------------------------------------------------------+
| Component                                      | VRAM Usage |
+-----------------------------------------------------------+
| Base Model (8B in 4-bit NF4)                   |   ~5.2 GiB |
| LoRA Trainable Parameters (~20M params in FP32)|   ~0.1 GiB |
| Optimizer States (paged_adamw_8bit)            |   ~0.2 GiB |
| Activations (Seq Len 2048, Grad Checkpointing) |   ~4.8 GiB |
| CUDA Kernels & PyTorch Context Overhead        |   ~0.8 GiB |
+-----------------------------------------------------------+
| Peak Memory Allocation                         |  ~11.1 GiB |
| Free Headroom (out of 15.0 GiB available)      |   ~3.9 GiB |
+-----------------------------------------------------------+
```

---

## 4. Reference Colab QLoRA Script

```python
"""Reference training script for QLoRA fine-tuning on Google Colab T4.

DO NOT RUN THIS SCRIPT UNTIL DIRECTED.
Prerequisites: pip install torch transformers peft bitsandbytes trl datasets accelerate
"""

import torch
from datasets import load_dataset
from peft import LoraConfig, get_peft_model, prepare_model_for_kbit_training
from transformers import (
    AutoModelForCausalLM,
    AutoTokenizer,
    BitsAndBytesConfig,
    TrainingArguments,
)
from trl import SFTTrainer

# 1. Configuration
MODEL_ID = "Qwen/Qwen2.5-7B-Instruct"  # or "meta-llama/Meta-Llama-3.1-8B-Instruct"
DATA_PATH = "generator/data/sft_train.jsonl"
OUTPUT_DIR = "models/qlora_adapter"

# 2. 4-bit BitsAndBytes Quantization Config
bnb_config = BitsAndBytesConfig(
    load_in_4bit=True,
    bnb_4bit_quant_type="nf4",
    bnb_4bit_compute_dtype=torch.float16,
    bnb_4bit_use_double_quant=True,
)

# 3. Load Tokenizer & Model
tokenizer = AutoTokenizer.from_pretrained(MODEL_ID, trust_remote_code=True)
tokenizer.pad_token = tokenizer.eos_token
tokenizer.padding_side = "right"

model = AutoModelForCausalLM.from_pretrained(
    MODEL_ID,
    quantization_config=bnb_config,
    device_map="auto",
    torch_dtype=torch.float16,
    trust_remote_code=True,
)
model = prepare_model_for_kbit_training(model)
model.config.use_cache = False

# 4. LoRA Adapter Configuration
peft_config = LoraConfig(
    r=16,
    lora_alpha=32,
    lora_dropout=0.05,
    bias="none",
    task_type="CAUSAL_LM",
    target_modules=[
        "q_proj",
        "k_proj",
        "v_proj",
        "o_proj",
        "gate_proj",
        "up_proj",
        "down_proj",
    ],
)

# 5. Format Dataset for SFT
dataset = load_dataset("json", data_files=DATA_PATH, split="train")

def format_prompts(example):
    # Formats messages according to the tokenizer chat template
    text = tokenizer.apply_chat_template(
        example["messages"],
        tokenize=False,
        add_generation_prompt=False,
    )
    return {"text": text}

dataset = dataset.map(format_prompts)

# 6. Training Arguments for T4
training_args = TrainingArguments(
    output_dir=OUTPUT_DIR,
    per_device_train_batch_size=1,
    gradient_accumulation_steps=16,
    learning_rate=2e-4,
    lr_scheduler_type="cosine",
    warmup_ratio=0.03,
    num_train_epochs=3,
    logging_steps=10,
    save_strategy="epoch",
    fp16=True,
    optim="paged_adamw_8bit",
    gradient_checkpointing=True,
    report_to="none",
)

# 7. Trainer Setup
trainer = SFTTrainer(
    model=model,
    train_dataset=dataset,
    peft_config=peft_config,
    dataset_text_field="text",
    max_seq_length=2048,
    tokenizer=tokenizer,
    args=training_args,
)

# 8. Train & Save Adapter
# trainer.train()
# trainer.model.save_pretrained(OUTPUT_DIR)
# tokenizer.save_pretrained(OUTPUT_DIR)
print(f"QLoRA recipe configured for {MODEL_ID}. DO NOT RUN WITHOUT EXPLICIT APPROVAL.")
```
