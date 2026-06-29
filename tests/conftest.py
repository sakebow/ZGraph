"""Shared pytest fixtures for ZGraph test suite.

These helpers isolate process environment between tests so that a test
setting ``DEEPSEEK_API_KEY`` cannot leak into the next test that asserts
the legacy default-provider mode is empty.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from zgraph.config import Settings


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


# ---------------------------------------------------------------------------
# 共享测试 fixture：真实 PNG + 隔离 settings
# ---------------------------------------------------------------------------


# 真实图片：用户提供的 PNG（1.5MB+）。所有 image_bytes 必须从这里取。
SAMPLE_IMAGE = (
    Path(__file__).resolve().parent.parent
    / ".zgraph"
    / "storage"
    / "examples"
    / "140037382_p0.png"
)


@pytest.fixture
def sample_png() -> bytes:
    """读真实 PNG bytes。前置检查：文件存在且 > 1MB。"""
    assert SAMPLE_IMAGE.exists(), f"missing sample image: {SAMPLE_IMAGE}"
    data = SAMPLE_IMAGE.read_bytes()
    assert len(data) > 1_000_000, f"expected > 1MB PNG, got {len(data)} bytes"
    return data


@pytest.fixture
def tmp_settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Settings:
    """构造指到 tmp_path 的 Settings（避免污染真实目录）。"""
    monkeypatch.setenv("ZGRAPH_TMP_STORE_PATH", str(tmp_path / "storage"))
    monkeypatch.setenv("ZGRAPH_OFFLINE", "true")
    return Settings.from_env()
