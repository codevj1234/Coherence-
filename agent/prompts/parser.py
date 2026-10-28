"""Robust parser for LLM-generated device configuration outputs.

Converts free-form model outputs into validated per-device configuration patches.
Tolerates common LLM generation quirks (conversational chatter, Windows CRLF,
leading whitespace, 4+ backticks, tildes) while strictly reporting anomalies
(unclosed fences, duplicate blocks, missing fences) to enable automated PARSE_FAIL classification.
"""

from dataclasses import dataclass, field
import re


@dataclass
class ParsedOutput:
    """Result of parsing an LLM response into per-device configurations."""

    devices: dict[str, str]
    errors: list[str]
    warnings: list[str] = field(default_factory=list)

    @property
    def is_ok(self) -> bool:
        """True if configuration was extracted with zero structural errors."""
        return len(self.errors) == 0 and len(self.devices) > 0


OPEN_FENCE_REGEX = re.compile(r"^(`{3,}|~{3,})(.*)$")
CLOSE_FENCE_REGEX = re.compile(r"^(`{3,}|~{3,})\s*$")


def parse_model_output(text: str) -> ParsedOutput:
    """Parse raw LLM output text into per-device configuration dictionaries.

    Guarantees:
    - Never raises exceptions on malformed model text.
    - Normalizes Windows (CRLF), Mac (CR), and mixed line endings to standard LF.
    - Supports backticks (``` or ````+) and tildes (~~~+) as code fence delimiters.
    - Extracts complete configs for each device in ```<device_name> ... ``` blocks.
    - Preserves exact device names written by the model (e.g. uppercase).
    - Ignores extra language tags or metadata following the device name on the opening fence.
    - Handles unclosed blocks, empty blocks, duplicate blocks, and missing blocks.
    - Preserves last instance on duplicates while recording an error.
    - Ignores conversational prose outside blocks while recording a warning.

    Args:
        text: Raw text string produced by the LLM.

    Returns:
        ParsedOutput containing parsed device configs, errors, and warnings.
    """
    devices: dict[str, str] = {}
    errors: list[str] = []
    warnings: list[str] = []

    if not text or not text.strip():
        return ParsedOutput(
            devices={},
            errors=["Empty model response; no device configurations found."],
            warnings=[],
        )

    # Normalize line endings
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = normalized.split("\n")

    inside_block = False
    current_device: str | None = None
    current_fence_char: str | None = None
    current_lines: list[str] = []
    found_any_fence = False

    for line_idx, line in enumerate(lines, start=1):
        stripped = line.strip()

        if not inside_block:
            m_open = OPEN_FENCE_REGEX.match(stripped)
            if m_open:
                found_any_fence = True
                fence_str = m_open.group(1)
                tag_content = m_open.group(2).strip()
                current_fence_char = fence_str[0]

                if not tag_content:
                    errors.append(f"Line {line_idx}: Code block opening fence is missing a device name.")
                    current_device = None
                else:
                    # If tag contains spaces/language tags (e.g. ```r1 ios), first token is device name
                    current_device = tag_content.split()[0]

                inside_block = True
                current_lines = []
            else:
                if stripped:
                    # Non-empty conversational prose outside code blocks
                    preview = stripped if len(stripped) <= 60 else f"{stripped[:57]}..."
                    warnings.append(f"Prose outside fenced blocks ignored at line {line_idx}: {preview}")
        else:
            # Check for closing fence matching opening delimiter character
            m_close = CLOSE_FENCE_REGEX.match(stripped)
            if m_close and m_close.group(1)[0] == current_fence_char:
                inside_block = False
                if current_device:
                    block_content = "\n".join(current_lines).strip()
                    if not block_content:
                        errors.append(f"Empty configuration block for device '{current_device}'.")
                    else:
                        if current_device in devices:
                            errors.append(
                                f"Duplicate configuration block for device '{current_device}'; last instance used."
                            )
                        devices[current_device] = block_content

                current_device = None
                current_fence_char = None
                current_lines = []
            else:
                current_lines.append(line)

    # Check for unclosed fence at EOF
    if inside_block:
        target_name = current_device or "unnamed"
        errors.append(f"Unclosed fenced code block for device '{target_name}'.")
        # Preserve accumulated content for harness inspection
        if current_device:
            block_content = "\n".join(current_lines).strip()
            if block_content:
                devices[current_device] = block_content

    # If no blocks were found at all
    if not found_any_fence:
        errors.append("No fenced configuration blocks found in model output.")
    elif not devices and not errors:
        errors.append("No valid device configurations could be extracted.")

    return ParsedOutput(devices=devices, errors=errors, warnings=warnings)
