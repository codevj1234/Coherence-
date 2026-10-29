"""Real-model wrappers for the harness (Phase 4).  Each exposes complete(prompt) -> text and last_usage.

  HFChatModel       - a local Hugging Face model (e.g. a 7-8B instruct model in 4-bit on a Colab T4).
  OpenAICompatModel - any OpenAI-compatible chat endpoint: a local vLLM server (fast on Colab: it batches the
                      requests of several sequences run in parallel), or a free hosted tier if you have a key.

Decoding is greedy (temperature 0) so a run is repeatable as far as the backend allows.
"""
from __future__ import annotations

import json
import time
import urllib.request
from typing import Optional, Tuple


class HFChatModel:
    """Local Hugging Face model, 4-bit by default.  Thread-safe and BATCHED: several sequences run in parallel
    threads (run_models.py --workers N) and their requests are generated together, which is several times faster
    on a T4 than one request at a time.  adapter = a PEFT/LoRA folder (the fine-tuned model of Phase 5)."""

    def __init__(self, model_id: str, name: Optional[str] = None, load_in_4bit: bool = True,
                 max_new_tokens: int = 2048, max_input_tokens: int = 12000, adapter: Optional[str] = None,
                 max_batch: int = 4, wait_s: float = 0.3):
        import queue, threading
        import torch
        from transformers import AutoModelForCausalLM, AutoTokenizer
        kw = dict(device_map="auto")
        if load_in_4bit:
            from transformers import BitsAndBytesConfig
            kw["quantization_config"] = BitsAndBytesConfig(load_in_4bit=True, bnb_4bit_quant_type="nf4",
                                                           bnb_4bit_compute_dtype=torch.float16)
        else:
            kw["torch_dtype"] = torch.float16
        self.tok = AutoTokenizer.from_pretrained(model_id)
        self.tok.padding_side = "left"
        if self.tok.pad_token is None:
            self.tok.pad_token = self.tok.eos_token
        self.model = AutoModelForCausalLM.from_pretrained(model_id, **kw)
        if adapter:
            from peft import PeftModel
            self.model = PeftModel.from_pretrained(self.model, adapter)
        self.model.eval()
        self.name = name or (model_id.split("/")[-1] + ("-ft" if adapter else ""))
        self.max_new_tokens, self.max_input_tokens = max_new_tokens, max_input_tokens
        self.max_batch, self.wait_s = max_batch, wait_s
        self._torch, self._q = torch, queue.Queue()
        self._local = threading.local()
        threading.Thread(target=self._serve, daemon=True).start()

    @property
    def last_usage(self) -> Optional[Tuple[int, int]]:
        return getattr(self._local, "usage", None)

    def complete(self, prompt: str) -> str:
        import threading
        slot = dict(prompt=prompt, done=threading.Event())
        self._q.put(slot)
        slot["done"].wait()
        if "error" in slot:
            raise slot["error"]
        self._local.usage = slot["usage"]
        return slot["text"]

    def _generate(self, batch):
        texts = [self.tok.apply_chat_template([{"role": "user", "content": b["prompt"]}],
                                              add_generation_prompt=True, tokenize=False) for b in batch]
        enc = self.tok(texts, return_tensors="pt", padding=True, add_special_tokens=False).to(self.model.device)
        if enc["input_ids"].shape[1] > self.max_input_tokens:
            raise ValueError(f"prompt too long: {enc['input_ids'].shape[1]} tokens")
        # a correct answer is never longer than the configs it was shown, so cap the output at the
        # prompt length + a margin: one rambling answer otherwise holds the whole batch for 2,048 tokens
        longest = int(enc["attention_mask"].sum(dim=1).max())
        cap = min(self.max_new_tokens, longest + 128)
        with self._torch.no_grad():
            out = self.model.generate(**enc, max_new_tokens=cap, do_sample=False,
                                      pad_token_id=self.tok.pad_token_id)
        width = enc["input_ids"].shape[1]
        for i, b in enumerate(batch):
            new = out[i, width:]
            n_out = int((new != self.tok.pad_token_id).sum())
            b["text"] = self.tok.decode(new, skip_special_tokens=True)
            b["usage"] = (int(enc["attention_mask"][i].sum()), n_out)
        del out, enc

    def _serve(self):
        import queue, time as _t
        while True:
            batch = [self._q.get()]
            deadline = _t.time() + self.wait_s
            while len(batch) < self.max_batch:
                try:
                    batch.append(self._q.get(timeout=max(0.0, deadline - _t.time())))
                except queue.Empty:
                    break
            try:
                self._generate(batch)
            except Exception as e:
                if "out of memory" in str(e).lower() and len(batch) > 1:
                    # long prompts (C3 ledger + feedback) do not fit three at a time on a T4: free the memory
                    # and generate them one by one instead of failing all of them
                    self._torch.cuda.empty_cache()
                    for b in batch:
                        try:
                            self._generate([b])
                        except Exception as e1:
                            self._torch.cuda.empty_cache()
                            b["error"] = e1
                else:
                    if "out of memory" in str(e).lower():
                        self._torch.cuda.empty_cache()
                    for b in batch:
                        b["error"] = e
            for b in batch:
                b["done"].set()


class OpenAICompatModel:
    """POST {base_url}/chat/completions.  For a local vLLM server: base_url='http://localhost:8000/v1'."""

    def __init__(self, base_url: str, model: str, api_key: str = "EMPTY", name: Optional[str] = None,
                 max_tokens: int = 2048, timeout: int = 600, retries: int = 4, min_interval_s: float = 0.0):
        self.url = base_url.rstrip("/") + "/chat/completions"
        self.model, self.key = model, api_key
        self.name = name or model.split("/")[-1]
        self.max_tokens, self.timeout, self.retries = max_tokens, timeout, retries
        self.min_interval_s = min_interval_s           # free hosted tiers: space out requests
        self._last = 0.0
        self.last_usage: Optional[Tuple[int, int]] = None

    def complete(self, prompt: str) -> str:
        body = json.dumps(dict(model=self.model, messages=[{"role": "user", "content": prompt}],
                               temperature=0, max_tokens=self.max_tokens)).encode()
        err = None
        for k in range(self.retries):
            wait = self.min_interval_s - (time.time() - self._last)
            if wait > 0:
                time.sleep(wait)
            self._last = time.time()
            req = urllib.request.Request(self.url, data=body, headers={
                "Content-Type": "application/json", "Authorization": f"Bearer {self.key}"})
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as r:
                    data = json.loads(r.read())
                u = data.get("usage") or {}
                self.last_usage = (u.get("prompt_tokens", 0), u.get("completion_tokens", 0)) if u else None
                return data["choices"][0]["message"]["content"] or ""
            except Exception as e:                       # rate limits / server warming up: back off and retry
                err = e
                time.sleep(min(60, 5 * 2 ** k))
        raise RuntimeError(f"model endpoint failed after {self.retries} tries: {err}")
