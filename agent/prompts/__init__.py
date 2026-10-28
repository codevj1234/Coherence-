"""Prompt construction, system templates, device resolution, and robust output parsing."""

from agent.prompts.templates import PROMPT_VERSION, SYSTEM_PROMPT
from agent.prompts.builder import PromptIntent, build_prompt
from agent.prompts.parser import ParsedOutput, parse_model_output
from agent.prompts.resolve import ResolvedOutput, resolve_devices

__all__ = [
    "PROMPT_VERSION",
    "ParsedOutput",
    "PromptIntent",
    "ResolvedOutput",
    "SYSTEM_PROMPT",
    "build_prompt",
    "parse_model_output",
    "resolve_devices",
]
