"""Tests for LLM configuration, client routing, mock adapter, and HTTP adapters."""

import os
from pathlib import Path
import pytest
import httpx

from agent.llm.config import (
    ModelConfig,
    ProviderType,
    get_model_config,
    load_model_configs,
)
from agent.llm.client import (
    GenerationResult,
    LLMClient,
    LLMGenerationError,
    MockAdapter,
    OpenAICompatibleAdapter,
    Prompt,
    generate,
)


def test_load_model_configs() -> None:
    configs = load_model_configs("configs/models.yaml")
    assert "mock" in configs
    assert "openai_gpt4o_mini" in configs

    cfg = get_model_config("mock", "configs/models.yaml")
    assert cfg.provider == ProviderType.MOCK
    assert cfg.mock_mode == "echo"


def test_get_nonexistent_model() -> None:
    with pytest.raises(KeyError) as excinfo:
        get_model_config("nonexistent_model_name", "configs/models.yaml")
    assert "Available models" in str(excinfo.value)


def test_load_nonexistent_config_file() -> None:
    with pytest.raises(FileNotFoundError):
        load_model_configs("nonexistent_file.yaml")


def test_mock_adapter_echo() -> None:
    adapter = MockAdapter()
    cfg = ModelConfig(
        name="mock",
        provider=ProviderType.MOCK,
        model_id="mock",
        mock_mode="echo",
    )
    prompt = Prompt(
        system="You are an engineer.",
        user="Configs:\n```r1\nhostname r1\ninterface eth0\n```\n```r2\nhostname r2\n```",
    )
    res = adapter.generate(prompt, cfg)
    assert "```r1" in res.text
    assert "```r2" in res.text
    assert "hostname r1" in res.text
    assert "hostname r2" in res.text
    assert res.tokens_in > 0
    assert res.tokens_estimated is True
    assert res.estimated_tokens is True


def test_mock_adapter_scripted_edit() -> None:
    adapter = MockAdapter()
    cfg = ModelConfig(
        name="mock",
        provider=ProviderType.MOCK,
        model_id="mock",
        mock_mode="scripted_edit",
    )
    prompt = Prompt(
        system="system",
        user="Configs:\n```r1\nhostname r1\n```",
    )
    res = adapter.generate(prompt, cfg)
    assert "mock scripted edit" in res.text
    assert "```r1" in res.text


def test_mock_adapter_malformed() -> None:
    adapter = MockAdapter()
    cfg = ModelConfig(
        name="mock",
        provider=ProviderType.MOCK,
        model_id="mock",
        mock_mode="malformed",
    )
    prompt = Prompt(system="sys", user="user")
    res = adapter.generate(prompt, cfg)
    # Malformed output lacks closing fence
    assert res.text.count("```") == 1


def test_openai_missing_api_key_env_var(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("NON_EXISTENT_KEY_ENV", raising=False)
    adapter = OpenAICompatibleAdapter()
    cfg = ModelConfig(
        name="test_openai",
        provider=ProviderType.OPENAI_COMPATIBLE,
        base_url="https://api.example.com/v1",
        api_key_env="NON_EXISTENT_KEY_ENV",
        model_id="test-model",
    )
    prompt = Prompt(system="sys", user="usr")
    with pytest.raises(ValueError) as excinfo:
        adapter.generate(prompt, cfg)
    assert "NON_EXISTENT_KEY_ENV" in str(excinfo.value)
    assert "missing" in str(excinfo.value).lower() or "not set" in str(excinfo.value).lower()


def test_openai_successful_response(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TEST_API_KEY", "dummy_secret_value")

    def mock_handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == "Bearer dummy_secret_value"
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {"content": "```r1\nhostname r1_updated\n```"},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {
                    "prompt_tokens": 150,
                    "completion_tokens": 40,
                },
            },
        )

    transport = httpx.MockTransport(mock_handler)
    with httpx.Client(transport=transport) as test_client:
        adapter = OpenAICompatibleAdapter(http_client=test_client)
        cfg = ModelConfig(
            name="test_mock_http",
            provider=ProviderType.OPENAI_COMPATIBLE,
            base_url="https://api.example.com/v1",
            api_key_env="TEST_API_KEY",
            model_id="gpt-test",
        )
        res = adapter.generate(Prompt(system="sys", user="user"), cfg)
        assert res.text == "```r1\nhostname r1_updated\n```"
        assert res.tokens_in == 150
        assert res.tokens_out == 40
        assert res.raw_finish_reason == "stop"
        assert res.tokens_estimated is False
        assert res.estimated_tokens is False


def test_openai_missing_usage_estimates_tokens(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TEST_API_KEY", "dummy")

    def mock_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {"content": "12345678"},  # 8 chars -> 2 estimated tokens
                        "finish_reason": "stop",
                    }
                ]
            },
        )

    transport = httpx.MockTransport(mock_handler)
    with httpx.Client(transport=transport) as test_client:
        adapter = OpenAICompatibleAdapter(http_client=test_client)
        cfg = ModelConfig(
            name="test",
            provider=ProviderType.OPENAI_COMPATIBLE,
            base_url="https://api.example.com/v1",
            api_key_env="TEST_API_KEY",
            model_id="test",
        )
        res = adapter.generate(Prompt(system="s", user="u"), cfg)
        assert res.text == "12345678"
        assert res.tokens_out == 2
        assert res.tokens_estimated is True
        assert res.estimated_tokens is True


def test_openai_retry_on_429(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TEST_API_KEY", "dummy")
    call_count = 0

    def mock_handler(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return httpx.Response(429, json={"error": "rate limit"})
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": "success after retry"}}]},
        )

    transport = httpx.MockTransport(mock_handler)
    with httpx.Client(transport=transport) as test_client:
        adapter = OpenAICompatibleAdapter(http_client=test_client, base_backoff_s=0.01)
        cfg = ModelConfig(
            name="test",
            provider=ProviderType.OPENAI_COMPATIBLE,
            base_url="https://api.example.com/v1",
            api_key_env="TEST_API_KEY",
            model_id="test",
        )
        res = adapter.generate(Prompt(system="s", user="u"), cfg)
        assert res.text == "success after retry"
        assert call_count == 2


def test_openai_non_retriable_400(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TEST_API_KEY", "dummy")

    def mock_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"error": "bad request"})

    transport = httpx.MockTransport(mock_handler)
    with httpx.Client(transport=transport) as test_client:
        adapter = OpenAICompatibleAdapter(http_client=test_client, base_backoff_s=0.01)
        cfg = ModelConfig(
            name="test",
            provider=ProviderType.OPENAI_COMPATIBLE,
            base_url="https://api.example.com/v1",
            api_key_env="TEST_API_KEY",
            model_id="test",
        )
        with pytest.raises(LLMGenerationError) as excinfo:
            adapter.generate(Prompt(system="s", user="u"), cfg)
        assert "Non-retriable" in str(excinfo.value)
