"""Shared pytest fixtures for ZGraph test suite.

These helpers isolate process environment between tests so that a test
setting ``DEEPSEEK_API_KEY`` cannot leak into the next test that asserts
the legacy default-provider mode is empty.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from typing import Any

import pytest


# All ZGraph env-var keys that participate in provider / runtime configuration.
# Keep this list in sync with zgraph.config and zgraph.core.provider as new
# variables are introduced.
_PROVIDER_ENV_KEYS: tuple[str, ...] = (
    # multi-provider
    "ZGRAPH_PROVIDERS",
    "ZGRAPH_DEFAULT_PROVIDER",
    # per-provider overrides
    "DEEPSEEK_API_KEY",
    "DEEPSEEK_MODEL",
    "DEEPSEEK_BASE_URL",
    "KIMI_API_KEY",
    "KIMI_MODEL",
    "KIMI_BASE_URL",
    "MINIMAX_API_KEY",
    "MINIMAX_MODEL",
    "MINIMAX_BASE_URL",
    # legacy single-provider
    "BASE_URL",
    "APIKEY",
    "API_KEY",
    "MODEL_NAME",
    "LLM_MODEL_NAME",
    "LLM_PROVIDER",
    "LLM_TIMEOUT",
    "LLM_TEMPERATURE",
    "LLM_TOP_P",
    "LLM_MAX_TOKENS",
    "LLM_REASONING_EFFORT",
    "STRUCTURED_OUTPUT",
)


@pytest.fixture(autouse=True)
def _isolate_provider_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Clear every provider-related env var before each test.

    Tests that need specific values should use ``set_env`` (or call
    ``monkeypatch.setenv`` directly) inside their own body — that way the
    cleanup runs automatically on teardown.
    """
    for key in _PROVIDER_ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    yield


@pytest.fixture
def set_env(monkeypatch: pytest.MonkeyPatch) -> Any:
    """Return a helper that bulk-sets env vars for the current test.

    用法::

        def test_x(set_env):
            set_env(ZGRAPH_PROVIDERS="kimi", KIMI_API_KEY="sk-test")
            ...

    设置会在测试结束后由 monkeypatch 自动清理。
    """

    def _set(**values: str) -> None:
        for key, value in values.items():
            monkeypatch.setenv(key, value)

    return _set


@pytest.fixture
def clean_environ(monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    """Return a snapshot of provider env vars AFTER cleanup.

    Useful for asserting which vars are present/absent in a given test.
    """
    for key in _PROVIDER_ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    return {k: os.environ.get(k, "") for k in _PROVIDER_ENV_KEYS}
