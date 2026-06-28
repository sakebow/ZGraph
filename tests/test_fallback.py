"""Integration tests for the provider fallback chain.

Each test isolates process environment via the ``_isolate_provider_env``
autouse fixture in ``conftest.py`` and asserts on the resolved chain order,
the resulting ``Runnable`` structure, and the failover behavior under
simulated provider failures.

No real HTTP calls are made; we mock the ``ChatOpenAI`` instances so that
``with_fallbacks`` can be exercised deterministically.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult

from zgraph.config import ProviderConfig, Settings
from zgraph.core.provider import (
    AllProvidersFailedError,
    _build_chain,
    build_chat_model_with_fallback,
    list_supported_providers,
    resolve_provider,
)


pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _settings_with(
    *,
    providers: dict[str, ProviderConfig] | None = None,
    legacy_provider: ProviderConfig | None = None,
    default_provider: str = "",
) -> Settings:
    """构造最小可用的 Settings 实例用于 fallback 链测试。

    只填与 fallback 链路相关的字段，其余保持空；该函数不调用 ``from_env``，
    因此不依赖任何 env 变量，避开 autouse fixture 的副作用。
    """
    s = Settings(
        base_url="",
        api_key="",
        model_name="",
        provider="",
        timeout=120,
        temperature=None,
        top_p=None,
        max_tokens=None,
        reasoning_effort="",
        structured_output=False,
        max_rounds=50,
        stream=True,
        system_prompt="",
        host="127.0.0.1",
        port=8001,
        zgraph_home=None,  # type: ignore[arg-type]
        data_dir=None,  # type: ignore[arg-type]
        layer_config=None,  # type: ignore[arg-type]
        skills_dir=None,  # type: ignore[arg-type]
        offline=False,
        skill_search=True,
        skill_top_k=4,
        skill_min_score=0.18,
        skill_context_char_limit=1200,
        tokenizer_strategy="word",
        rerank_model_name="",
        rerank_base_url="",
        rerank_api_key="",
        rerank_timeout=30,
        rerank_document_char_limit=1024,
        rerank_batch_size=4,
        tool_top_k=4,
        tool_min_score=0.18,
        memory_summary_max_tokens=256,
        memory_summary_temperature=0.2,
        log_level="INFO",
        log_enabled=True,
        whitelist=set(),
        run_ttl_seconds=86400,
        allow_bash=False,
        auto_approve_interrupts=False,
        providers=providers or {},
        default_provider=default_provider,
        legacy_provider=legacy_provider,
        tmp_store_path=Path("./storage"),
        media_ttl_seconds=3600,
    )
    return s


def _provider(name: str, api_key: str = "sk-test") -> ProviderConfig:
    return ProviderConfig(
        name=name,
        api_key=api_key,
        base_url=f"https://{name}.example.com/v1",
        model=f"{name}-model",
    )


class _RaisingFakeChatModel(GenericFakeChatModel):
    """GenericFakeChatModel that raises a configured exception on invoke().

    继承自真正的 LangChain fake chat model，因此 ``with_fallbacks`` 走真实
    Runnable 协议，能真正验证 LangChain 的 fallback 实现而不是我们的 mock。
    """

    def __init__(
        self,
        *,
        raises: BaseException | None = None,
        returns: str = "ok",
    ) -> None:
        super().__init__(messages=iter([returns]))
        self._raises = raises

    def _generate(
        self,
        messages: list,
        stop: list[str] | None = None,
        run_manager: Any = None,
        **kwargs: Any,
    ) -> ChatResult:
        if self._raises is not None:
            raise self._raises
        message = next(self.messages)
        message_ = AIMessage(content=message) if isinstance(message, str) else message
        generation = ChatGeneration(message=message_)
        return ChatResult(generations=[generation])


# ---------------------------------------------------------------------------
# 1. Chain ordering
# ---------------------------------------------------------------------------


class TestFallbackChainOrder:
    """__legacy__ 永远第一；其后按 ZGRAPH_PROVIDERS 声明顺序（即 dict 插入顺序）。"""

    def test_legacy_alone(self):
        s = _settings_with(legacy_provider=_provider("__legacy__"))
        assert _build_chain(s) == ["__legacy__"]

    def test_multi_provider_only(self):
        s = _settings_with(
            providers={
                "deepseek": _provider("deepseek"),
                "kimi": _provider("kimi"),
                "minimax": _provider("minimax"),
            }
        )
        # ZGRAPH_PROVIDERS 解析时按 "deepseek,kimi,minimax" 的顺序插入；
        # 这里手动模拟这个顺序。
        assert _build_chain(s) == ["deepseek", "kimi", "minimax"]

    def test_legacy_takes_priority_over_multi(self):
        """用户的要求：BASE_URL 比 ZGRAPH_PROVIDERS 更优先。"""
        s = _settings_with(
            providers={
                "deepseek": _provider("deepseek"),
                "kimi": _provider("kimi"),
            },
            legacy_provider=_provider("__legacy__"),
        )
        chain = _build_chain(s)
        assert chain[0] == "__legacy__"
        assert chain == ["__legacy__", "deepseek", "kimi"]

    def test_empty_chain_when_nothing_configured(self):
        s = _settings_with()
        assert _build_chain(s) == []

    def test_explicit_primary_moves_to_front(self):
        s = _settings_with(
            providers={
                "deepseek": _provider("deepseek"),
                "kimi": _provider("kimi"),
            },
            legacy_provider=_provider("__legacy__"),
        )
        chain = _build_chain(s, primary_name="kimi")
        # kimi 提到第一，但其它保持原顺序
        assert chain == ["kimi", "__legacy__", "deepseek"]

    def test_explicit_primary_not_in_chain_is_ignored(self):
        s = _settings_with(
            providers={"deepseek": _provider("deepseek")},
            legacy_provider=_provider("__legacy__"),
        )
        chain = _build_chain(s, primary_name="minimax")  # not in chain
        # primary_name 既然不在链里，就保持原顺序
        assert chain == ["__legacy__", "deepseek"]

    def test_chain_does_not_duplicate_legacy_and_default(self):
        """即使 legacy_provider 与 providers["default"] 重复，也只出现一次。"""
        # 当前实现下，二者并存仅出现在 mode A（legacy-only），
        # 解析逻辑不会让两者同时出现；但 _build_chain 应该安全处理。
        s = _settings_with(
            providers={"__legacy__": _provider("__legacy__")},
            legacy_provider=_provider("__legacy__"),
        )
        chain = _build_chain(s)
        assert chain.count("__legacy__") == 1


# ---------------------------------------------------------------------------
# 2. resolve_provider handles __legacy__
# ---------------------------------------------------------------------------


class TestResolveLegacy:
    def test_resolve_legacy_name(self):
        s = _settings_with(legacy_provider=_provider("__legacy__", api_key="sk-x"))
        cfg = resolve_provider(s, "__legacy__")
        assert cfg.name == "__legacy__"
        assert cfg.api_key == "sk-x"

    def test_resolve_legacy_without_config_raises(self):
        s = _settings_with()
        with pytest.raises(ValueError, match="__legacy__"):
            resolve_provider(s, "__legacy__")


# ---------------------------------------------------------------------------
# 3. build_chat_model_with_fallback — chain construction
# ---------------------------------------------------------------------------


class TestBuildChatModelWithFallback:
    """构造 Runnable 的结构正确性。"""

    def test_single_provider_chain(self):
        s = _settings_with(providers={"deepseek": _provider("deepseek", "sk-d")})
        m = build_chat_model_with_fallback(s)
        # 没有 fallback 时，with_fallbacks([]) 仍然返回一个 Runnable
        assert m is not None

    def test_multi_provider_chain_attached_metadata(self):
        s = _settings_with(
            providers={
                "deepseek": _provider("deepseek", "sk-d"),
                "kimi": _provider("kimi", "sk-k"),
            },
            legacy_provider=_provider("__legacy__", "sk-L"),
        )
        m = build_chat_model_with_fallback(s)
        # 我们在 provider.py 里 attach 的 metadata
        assert getattr(m, "_zgraph_fallback_chain", None) == [
            "__legacy__",
            "deepseek",
            "kimi",
        ]
        assert getattr(m, "_zgraph_primary", None) == "__legacy__"
        assert getattr(m, "_zgraph_skipped", None) == []

    def test_provider_without_api_key_is_skipped(self):
        s = _settings_with(
            providers={
                "deepseek": _provider("deepseek", ""),  # no key
                "kimi": _provider("kimi", "sk-k"),
            },
        )
        m = build_chat_model_with_fallback(s)
        assert getattr(m, "_zgraph_fallback_chain", None) == ["kimi"]
        assert getattr(m, "_zgraph_skipped", None) == ["deepseek"]

    def test_empty_chain_raises(self):
        s = _settings_with()
        with pytest.raises(ValueError, match="no providers configured"):
            build_chat_model_with_fallback(s)

    def test_all_providers_missing_key_raises(self):
        s = _settings_with(
            providers={
                "deepseek": _provider("deepseek", ""),
                "kimi": _provider("kimi", ""),
            }
        )
        with pytest.raises(ValueError, match="no provider in chain has an API key"):
            build_chat_model_with_fallback(s)

    def test_legacy_skipped_when_no_key(self):
        s = _settings_with(
            providers={"deepseek": _provider("deepseek", "sk-d")},
            legacy_provider=_provider("__legacy__", ""),
        )
        m = build_chat_model_with_fallback(s)
        # legacy 因为没 key 被跳过，deepseek 成为实际主 provider
        assert getattr(m, "_zgraph_fallback_chain", None) == ["deepseek"]
        assert getattr(m, "_zgraph_skipped", None) == ["__legacy__"]


# ---------------------------------------------------------------------------
# 4. Failover behavior — 用 GenericFakeChatModel 模拟 invoke 失败
# ---------------------------------------------------------------------------


class TestFailoverBehavior:
    """验证 fallback 真的能在 primary 抛异常时切换到下一个。

    使用 ``_RaisingFakeChatModel`` 而不是 ``MagicMock``：前者继承真正的
    LangChain fake chat model，能让 ``with_fallbacks`` 走真实的 Runnable
    协议，从而真正验证 LangChain 的 fallback 行为。
    """

    def _fake_request(self) -> Any:
        return MagicMock()

    def _fake_response(self) -> Any:
        return MagicMock()

    def test_api_connection_error_triggers_fallback(self):
        from openai import APIConnectionError

        primary = _RaisingFakeChatModel(
            raises=APIConnectionError(
                message="connection refused", request=self._fake_request()
            )
        )
        fallback = _RaisingFakeChatModel(returns="from fallback")

        wrapped = primary.with_fallbacks(
            [fallback],
            exceptions_to_handle=(APIConnectionError,),
        )
        result = wrapped.invoke("hello")
        assert isinstance(result, AIMessage)
        assert result.content == "from fallback"

    def test_bad_request_does_not_trigger_fallback(self):
        from openai import APIConnectionError, BadRequestError

        primary = _RaisingFakeChatModel(
            raises=BadRequestError(
                "invalid model", response=self._fake_response(), body=None
            )
        )
        fallback = _RaisingFakeChatModel(returns="should not be called")

        wrapped = primary.with_fallbacks(
            [fallback],
            exceptions_to_handle=(APIConnectionError,),
        )
        # BadRequestError 不在 exceptions_to_handle 中，直接抛出
        with pytest.raises(BadRequestError):
            wrapped.invoke("hello")

    def test_rate_limit_triggers_fallback(self):
        from openai import RateLimitError

        primary = _RaisingFakeChatModel(
            raises=RateLimitError(
                "rate limited",
                response=self._fake_response(),
                body=None,
            )
        )
        fallback = _RaisingFakeChatModel(returns="after rate limit")

        wrapped = primary.with_fallbacks(
            [fallback],
            exceptions_to_handle=(RateLimitError,),
        )
        result = wrapped.invoke("hello")
        assert result.content == "after rate limit"

    def test_all_providers_failed_error_format(self):
        err1 = RuntimeError("first")
        err2 = RuntimeError("second")
        e = AllProvidersFailedError([("a", err1), ("b", err2)])
        msg = str(e)
        assert "a" in msg
        assert "RuntimeError" in msg
        assert "first" in msg
        assert "b" in msg
        assert "second" in msg
        assert e.errors == [("a", err1), ("b", err2)]
