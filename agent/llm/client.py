"""LLM client interface, provider adapters, and deterministic mock implementation.

Supports both OpenAI-compatible endpoints (hosted APIs, local Ollama, vLLM)
and an offline deterministic mock adapter for testing without external dependencies.
"""

from dataclasses import dataclass
import os
import random
import re
import time
from typing import Any
import httpx

from agent.llm.config import ModelConfig, ProviderType


@dataclass(frozen=True)
class Prompt:
    """Immutable representation of a multi-part LLM prompt."""

    system: str
    user: str


@dataclass(frozen=True)
class GenerationResult:
    """Normalized output from an LLM inference call."""

    text: str
    tokens_in: int
    tokens_out: int
    latency_ms: int
    raw_finish_reason: str | None
    tokens_estimated: bool = False

    @property
    def estimated_tokens(self) -> bool:
        """Backward-compatibility alias for tokens_estimated."""
        return self.tokens_estimated


class LLMGenerationError(RuntimeError):
    """Raised when an LLM generation call permanently fails after retries."""


class MockAdapter:
    """Deterministic mock provider adapter for offline testing and dry runs.

    Requires zero network access and zero API keys. Supports three deterministic behaviors:
    1. 'echo': Preserves and echoes back current device configs without modification.
    2. 'scripted_edit': Injects a deterministic configuration line into the first device.
    3. 'malformed': Emits an unclosed or fence-less output to test harness error handling.
    """

    def generate(self, prompt: Prompt, cfg: ModelConfig) -> GenerationResult:
        start_time = time.perf_counter()
        mode = cfg.mock_mode or "echo"

        # Extract device blocks from user prompt if present: ```<device>\n<config>\n```
        fenced_pattern = re.compile(r"```([a-zA-Z0-9_\-\.]+)\n(.*?)```", re.DOTALL)
        matches = fenced_pattern.findall(prompt.user)

        if mode == "malformed":
            # Broken output: missing closing backticks and missing fence on second device
            text = "I have updated the router configurations:\n```r1\nhostname r1\ninterface GigabitEthernet0/0\n"
        elif mode == "scripted_edit":
            if matches:
                # Modify the first device with a deterministic comment/rule, keep others unchanged
                blocks: list[str] = []
                first = True
                for dev_name, config in matches:
                    clean_cfg = config.rstrip()
                    if first:
                        edited_cfg = f"{clean_cfg}\n! mock scripted edit step\nip access-list extended MOCK_INSPECT\n permit ip any any\n"
                        blocks.append(f"```{dev_name}\n{edited_cfg}```")
                        first = False
                    else:
                        blocks.append(f"```{dev_name}\n{clean_cfg}\n```")
                text = "\n\n".join(blocks)
            else:
                text = "```r1\n! fallback mock edit\n```"
        else:
            # Echo mode: reproduce identical fenced configs
            if matches:
                blocks = [f"```{dev}\n{cfg.strip()}\n```" for dev, cfg in matches]
                text = "\n\n".join(blocks)
            else:
                text = "```r1\n!\n```"

        elapsed_ms = max(1, int((time.perf_counter() - start_time) * 1000))
        prompt_chars = len(prompt.system) + len(prompt.user)
        tokens_in = max(1, prompt_chars // 4)
        tokens_out = max(1, len(text) // 4)

        return GenerationResult(
            text=text,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            latency_ms=elapsed_ms,
            raw_finish_reason="stop",
            tokens_estimated=True,
        )


class OpenAICompatibleAdapter:
    """Adapter for OpenAI-compatible Chat Completion HTTP endpoints.

    Compatible with hosted OpenAI endpoints as well as self-hosted servers
    like Ollama, vLLM, and LM-Deploy. Includes exponential backoff with jitter
    for rate limits and transient 5xx server errors.
    """

    def __init__(self, http_client: httpx.Client | None = None, base_backoff_s: float = 0.5) -> None:
        self._client = http_client
        self._base_backoff_s = base_backoff_s

    def _resolve_api_key(self, cfg: ModelConfig) -> str | None:
        """Fetch the API key from environment, guaranteeing no key is logged."""
        if not cfg.api_key_env:
            return None

        key = os.environ.get(cfg.api_key_env)
        if not key or not key.strip():
            raise ValueError(
                f"Missing required API key: environment variable '{cfg.api_key_env}' "
                f"for model '{cfg.name}' is not set or empty."
            )
        return key.strip()

    def generate(self, prompt: Prompt, cfg: ModelConfig) -> GenerationResult:
        if not cfg.base_url:
            raise ValueError(f"Model '{cfg.name}' is configured for openai_compatible but lacks base_url.")

        base_url = cfg.base_url.rstrip("/")
        endpoint = f"{base_url}/chat/completions"

        api_key = self._resolve_api_key(cfg)
        headers = {
            "Content-Type": "application/json",
        }
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"

        payload: dict[str, Any] = {
            "model": cfg.model_id,
            "messages": [
                {"role": "system", "content": prompt.system},
                {"role": "user", "content": prompt.user},
            ],
            "temperature": cfg.temperature,
            "max_tokens": cfg.max_tokens,
        }
        if cfg.seed is not None:
            payload["seed"] = cfg.seed

        max_retries = 5
        client = self._client or httpx.Client(timeout=cfg.timeout_s)
        owns_client = self._client is None

        start_time = time.perf_counter()
        last_error: Exception | None = None

        try:
            for attempt in range(max_retries + 1):
                try:
                    response = client.post(endpoint, json=payload, headers=headers)
                    if response.status_code == 429 or response.status_code >= 500:
                        response.raise_for_status()

                    response.raise_for_status()
                    data = response.json()

                    choices = data.get("choices", [])
                    if not choices:
                        raise LLMGenerationError(f"Response from {endpoint} contained no choices: {data}")

                    first_choice = choices[0]
                    message = first_choice.get("message", {})
                    text = message.get("content", "")
                    raw_finish_reason = first_choice.get("finish_reason")

                    usage = data.get("usage")
                    estimated_tokens = False
                    if usage and "prompt_tokens" in usage and "completion_tokens" in usage:
                        tokens_in = int(usage["prompt_tokens"])
                        tokens_out = int(usage["completion_tokens"])
                    else:
                        tokens_in = max(1, (len(prompt.system) + len(prompt.user)) // 4)
                        tokens_out = max(1, len(text) // 4)
                        estimated_tokens = True

                    elapsed_ms = max(1, int((time.perf_counter() - start_time) * 1000))
                    return GenerationResult(
                        text=text,
                        tokens_in=tokens_in,
                        tokens_out=tokens_out,
                        latency_ms=elapsed_ms,
                        raw_finish_reason=raw_finish_reason,
                        tokens_estimated=estimated_tokens,
                    )

                except (httpx.TimeoutException, httpx.HTTPStatusError) as exc:
                    is_retriable = isinstance(exc, httpx.TimeoutException) or (
                        isinstance(exc, httpx.HTTPStatusError)
                        and (exc.response.status_code == 429 or exc.response.status_code >= 500)
                    )
                    last_error = exc

                    if not is_retriable:
                        status_code = getattr(getattr(exc, "response", None), "status_code", "unknown")
                        raise LLMGenerationError(
                            f"Non-retriable HTTP {status_code} error from {endpoint}: {exc}"
                        ) from exc

                    if attempt < max_retries:
                        backoff = self._base_backoff_s * (2**attempt) + random.uniform(0.01, 0.1)
                        time.sleep(backoff)
                    else:
                        break

                except Exception as exc:
                    raise LLMGenerationError(f"Unexpected error calling {endpoint}: {exc}") from exc

            raise LLMGenerationError(
                f"Failed to generate after {max_retries} retries against {endpoint}. Last error: {last_error}"
            ) from last_error

        finally:
            if owns_client:
                client.close()


class LLMClient:
    """Unified client routing requests to the appropriate provider adapter."""

    def __init__(self, http_client: httpx.Client | None = None, base_backoff_s: float = 0.5) -> None:
        self.mock_adapter = MockAdapter()
        self.openai_adapter = OpenAICompatibleAdapter(
            http_client=http_client,
            base_backoff_s=base_backoff_s,
        )

    def generate(self, prompt: Prompt, cfg: ModelConfig) -> GenerationResult:
        """Route prompt to configured provider and return standardized result."""
        if cfg.provider == ProviderType.MOCK:
            return self.mock_adapter.generate(prompt, cfg)
        if cfg.provider == ProviderType.OPENAI_COMPATIBLE:
            return self.openai_adapter.generate(prompt, cfg)
        raise ValueError(f"Unsupported provider type: {cfg.provider}")


def generate(prompt: Prompt, cfg: ModelConfig) -> GenerationResult:
    """Convenience top-level function to generate LLM completions."""
    client = LLMClient()
    return client.generate(prompt, cfg)
