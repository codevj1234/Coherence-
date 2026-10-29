"""Model wrappers.

SimModel is a *simulated* LLM: a seeded stand-in whose error rates are
parameters, used only to exercise and demonstrate the harness and guard.  Its
numbers are NOT measurements of any real model.  GenericLLM is the adapter a
real model plugs into in Phase 4 (same propose() interface).
"""
from __future__ import annotations

import hashlib
import random
import re
from typing import Callable, Dict, Optional

from .intents import apply_intent

Texts = Dict[str, str]


def fence(outputs: Texts) -> str:
    return "\n".join(f"```{dev}\n{text}```" for dev, text in outputs.items())


def _rng(*parts) -> random.Random:
    h = hashlib.sha256("|".join(map(str, parts)).encode()).hexdigest()
    return random.Random(int(h[:12], 16))


class GoldModel:
    name = "gold"

    def propose(self, state: Texts, intent: dict, feedback: Optional[str], attempt: int) -> str:
        return fence(intent["gold_patch"])


class CorruptedGoldModel:
    """Gold patches, but chosen steps are corrupted on purpose in ways that MUST be detected."""
    name = "gold-corrupted"
    KINDS = ["delete_new_line", "delete_first_acl_line", "syntax_error", "flip_action"]

    def __init__(self, corrupt_steps):
        self.corrupt = set(corrupt_steps)

    def propose(self, state, intent, feedback, attempt):
        if intent["step"] not in self.corrupt:
            return fence(intent["gold_patch"])
        kind = self.KINDS[sorted(self.corrupt).index(intent["step"]) % len(self.KINDS)]
        p = intent["params"]
        dev = p["device"]
        text = intent["gold_patch"][dev]
        lines = text.split("\n")
        acl_idx = [i for i, l in enumerate(lines) if l.startswith("ip access-list extended")]
        # the ACL block in the gold patch = lines after the header until '!'
        start = next(i for i in acl_idx if lines[i].endswith(p["acl"])) + 1
        end = next(i for i in range(start, len(lines)) if lines[i].strip() == "!")
        block = lines[start:end]
        action = p["action"]
        from .intents import _wild
        target = f"{action} tcp {_wild(p['source'])} {_wild(p['destination'])} eq {p['port']}"
        new_line = next(l for l in block if l.strip().split(" ", 1)[1] == target)   # the NEW line, not a look-alike
        if kind == "delete_new_line":
            block.remove(new_line)
        elif kind == "delete_first_acl_line":
            block.pop(0)
        elif kind == "syntax_error":
            block[block.index(new_line)] = new_line.replace(action, action + "t", 1)
        elif kind == "flip_action":
            flipped = "deny" if action == "permit" else "permit"
            block[block.index(new_line)] = new_line.replace(action, flipped, 1)
        lines[start:end] = block
        return fence({dev: "\n".join(lines)})


class SimModel:
    """Simulated LLM.  Per call it applies the intent correctly, or makes one of three plausible
    mistakes (over-broad rule, rule in the wrong position, syntax error).  With feedback from a
    failed attempt it repairs the mistake with probability p_fix (a simulation assumption)."""

    def __init__(self, name: str, p_broad: float, p_misplace: float, p_syntax: float, p_fix: float = 0.6):
        self.name, self.p_broad, self.p_misplace, self.p_syntax, self.p_fix = name, p_broad, p_misplace, p_syntax, p_fix

    def propose(self, state: Texts, intent: dict, feedback: Optional[str], attempt: int) -> str:
        # Randomness depends only on (model, sequence, step, attempt): the first attempt is identical
        # in every condition, so conditions are compared on exactly the same model behaviour.
        r = _rng(self.name, intent["sequence_id"], intent["step"], attempt)
        if attempt > 0 and feedback and r.random() < self.p_fix:
            kw = {}
        else:
            r2 = _rng(self.name, intent["sequence_id"], intent["step"], attempt, "err")
            if r2.random() < self.p_syntax:
                kw = dict(syntax_error=True)
            else:
                kw = dict(broad=r2.random() < self.p_broad, misplaced=r2.random() < self.p_misplace)
        p = intent["params"]
        new = apply_intent(state, p, **kw)
        changed = {d: new[d] for d in sorted(new) if new[d] != state.get(d)}
        return fence(changed or {p["device"]: new[p["device"]]})


class GenericLLM:
    """Plug a real model in here: generate(prompt:str)->str.  Used in Phase 4 (API or local 7-8B)."""

    def __init__(self, name: str, generate: Callable[[str], str], build_prompt):
        self.name, self._gen, self._bp = name, generate, build_prompt

    def propose(self, state, intent, feedback, attempt):
        return self._gen(self._bp(state, intent, feedback))
