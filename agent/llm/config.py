"""Configuration parsing and schema validation for LLM providers.

Loads YAML configuration files into strongly-typed Pydantic models.
Enforces that secret keys are referenced only by environment variable names
rather than hardcoded values to prevent accidental credential leakage in logs or git.
"""

from enum import StrEnum
from pathlib import Path
from typing import Literal
import yaml
from pydantic import BaseModel, ConfigDict, Field


class ProviderType(StrEnum):
    """Supported LLM backend provider types."""

    OPENAI_COMPATIBLE = "openai_compatible"
    MOCK = "mock"


class ModelConfig(BaseModel):
    """Configuration settings for a single LLM model target."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(description="Unique configuration profile name.")
    provider: ProviderType = Field(description="Backend protocol adapter (openai_compatible or mock).")
    base_url: str | None = Field(
        default=None,
        description="Base URL for OpenAI-compatible API endpoint (e.g. http://localhost:11434/v1).",
    )
    api_key_env: str | None = Field(
        default=None,
        description="Environment variable name that holds the API key (never the raw key).",
    )
    model_id: str = Field(description="Upstream model identifier passed to the endpoint.")
    temperature: float = Field(default=0.0, ge=0.0, le=2.0, description="Sampling temperature.")
    max_tokens: int = Field(default=4096, gt=0, description="Maximum token generation limit.")
    seed: int | None = Field(default=None, description="Optional seed for deterministic sampling.")
    timeout_s: float = Field(default=30.0, gt=0, description="Network request timeout in seconds.")
    mock_mode: Literal["echo", "scripted_edit", "malformed"] | None = Field(
        default="echo",
        description="Behavior mode for mock provider (echo, scripted_edit, malformed).",
    )


def load_model_configs(config_path: str | Path = "configs/models.yaml") -> dict[str, ModelConfig]:
    """Load and validate all model configurations from a YAML file.

    Args:
        config_path: Path to the models.yaml file.

    Returns:
        Mapping from configuration name to validated ModelConfig instance.

    Raises:
        FileNotFoundError: If the config file cannot be found.
        ValueError: If YAML parsing fails or configurations violate schema.
    """
    path = Path(config_path)
    if not path.is_file():
        raise FileNotFoundError(f"Model configuration file not found: {path}")

    with path.open("r", encoding="utf-8") as f:
        try:
            raw_data = yaml.safe_load(f)
        except yaml.YAMLError as err:
            raise ValueError(f"Failed to parse YAML from {path}: {err}") from err

    if not isinstance(raw_data, dict) or "models" not in raw_data:
        raise ValueError(f"Invalid model configuration file '{path}': missing top-level 'models' dict.")

    models_dict = raw_data["models"]
    if not isinstance(models_dict, dict):
        raise ValueError(f"Invalid 'models' section in '{path}': expected mapping.")

    parsed_configs: dict[str, ModelConfig] = {}
    for key, item in models_dict.items():
        if not isinstance(item, dict):
            raise ValueError(f"Model entry '{key}' must be a dictionary.")
        cfg = ModelConfig.model_validate(item)
        parsed_configs[key] = cfg

    return parsed_configs


def get_model_config(name: str, config_path: str | Path = "configs/models.yaml") -> ModelConfig:
    """Retrieve a single ModelConfig by name.

    Args:
        name: Name of the model configuration entry.
        config_path: Path to the models.yaml file.

    Returns:
        Validated ModelConfig instance.

    Raises:
        KeyError: If the requested model name is not defined in the configuration file.
    """
    configs = load_model_configs(config_path)
    if name not in configs:
        available = ", ".join(configs.keys())
        raise KeyError(f"Model '{name}' not found in '{config_path}'. Available models: {available}")
    return configs[name]
