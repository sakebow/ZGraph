from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from zgraph.config import ProviderConfig, Settings
from langchain_openai import ChatOpenAI

# openai SDK 提供用于"应触发 fallback"的网络/服务端异常分类。
# 不在此元组中的异常（如 BadRequestError）意味着请求本身有业务问题，
# fallback 到下一个 provider 也救不回来，应该直接抛出。
from openai import (
    APIConnectionError,
    APITimeoutError,
    InternalServerError,
    RateLimitError,
)


# 触发 fallback 的异常类集合。Auth/Permission 错误也包含在内：若一个 provider
# 拒绝 key 而下一个接受了，至少能跑通；全拒了则由 AllProvidersFailedError 汇总。
_FALLBACK_EXCEPTIONS: tuple[type[BaseException], ...] = (
    APIConnectionError,
    APITimeoutError,
    RateLimitError,
    InternalServerError,
)


class AllProvidersFailedError(RuntimeError):
    """Fallback 链上所有 provider 都失败时抛出。

    携带每个 provider 的错误摘要，便于上层一次性上报或写入 audit 日志。
    """

    def __init__(self, errors: list[tuple[str, BaseException]]):
        self.errors = errors
        summary = "; ".join(f"{name}: {type(err).__name__}: {err}" for name, err in errors)
        super().__init__(f"All providers failed: {summary}")


@dataclass(slots=True, frozen=True)
class ProviderPreset:
    """Provider 内置预设。

    ``base_url`` 与 ``default_model`` 是 ZGraph 已知的默认配置；
    用户可通过 ``<PROVIDER>_BASE_URL`` 与 ``<PROVIDER>_MODEL`` 环境变量覆盖。
    """

    name: str
    base_url: str
    default_model: str


# 现阶段仅支持以下三个 provider。所有调用都走 OpenAI 兼容协议，
# 因此仅在 base_url / 默认 model 上做区分。
# 注意：此处统一使用小写 key，与 env 变量 ``ZGRAPH_PROVIDERS``、``MiniMax`` 等
# 内部归一化逻辑保持一致。
PROVIDER_PRESETS: dict[str, ProviderPreset] = {
    "deepseek": ProviderPreset(
        name="deepseek",
        base_url="https://api.deepseek.com/v1",
        default_model="deepseek-chat",
    ),
    "minimax": ProviderPreset(
        name="minimax",
        base_url="https://api.minimax.chat/v1",
        default_model="MiniMax-Text-01",
    ),
    "kimi": ProviderPreset(
        name="kimi",
        base_url="https://api.moonshot.cn/v1",
        default_model="moonshot-v1-8k",
    ),
}


def list_supported_providers() -> list[str]:
    """返回当前 ZGraph 支持的 provider 名称列表。

    返回:
        按字母顺序排序的 provider 名称列表。
    """
    return sorted(PROVIDER_PRESETS.keys())


def resolve_provider(settings: Settings, provider_name: Optional[str] = None) -> ProviderConfig:
    """根据 provider 名称从 ``Settings`` 中查找对应配置。

    参数:
        settings: 全局配置。
        provider_name: provider 名称；为 None 时使用 ``settings.default_provider``。

    返回:
        命中的 ``ProviderConfig``。

    异常:
        ValueError: 当 ``provider_name`` 指向未知 provider 或未启用 provider 时。
    """
    target = (provider_name or settings.default_provider or "").strip().lower()
    if not target:
        raise ValueError(
            "No default provider configured. Set ZGRAPH_DEFAULT_PROVIDER "
            "or enable at least one provider via ZGRAPH_PROVIDERS."
        )
    if target == "__legacy__":
        if settings.legacy_provider is None:
            available = _available_provider_summary(settings)
            raise ValueError(
                f"Provider '__legacy__' is not configured. Available: {available}."
            )
        return settings.legacy_provider
    config = settings.providers.get(target)
    if config is None:
        available = _available_provider_summary(settings)
        raise ValueError(
            f"Provider {target!r} is not configured. Available: {available}."
        )
    return config


def _available_provider_summary(settings: Settings) -> str:
    names = list(settings.providers.keys())
    if settings.legacy_provider is not None:
        names.append("__legacy__")
    return ", ".join(sorted(names)) or "<none>"


def _is_same_provider_config(
    cfg: ProviderConfig, other: Optional[ProviderConfig]
) -> bool:
    """判断两个 ProviderConfig 是否指向同一物理 provider。

    当 legacy_provider 与 providers 字典中由 BASE_URL 合成出来的 ``default``
    同时存在时（mode A 兼容路径），它们其实是同一份配置；fallback 链不应该
    重复试两次。
    """
    if other is None:
        return False
    return (
        cfg.api_key == other.api_key
        and cfg.base_url == other.base_url
        and cfg.model == other.model
    )


def _build_chain(settings: Settings, primary_name: Optional[str] = None) -> list[str]:
    """根据 settings 构造 fallback 链（provider 名称列表，按调用顺序）。

    链顺序：
    1. ``__legacy__``（如果 ``settings.legacy_provider`` 存在）—— 永远第一
    2. ``settings.providers`` 的 key，按插入顺序（即 ``ZGRAPH_PROVIDERS`` 声明顺序），
       其中与 ``legacy_provider`` 配置相同的项会被去重
    3. 若 ``primary_name`` 显式指定，则把它移到第一位（仍保留其它顺序）
    """
    chain: list[str] = []
    legacy = settings.legacy_provider
    if legacy is not None:
        chain.append("__legacy__")
    for name, cfg in settings.providers.items():
        if name in chain:
            continue
        if _is_same_provider_config(cfg, legacy):
            # legacy 已经覆盖了这条配置，跳过避免重复
            continue
        chain.append(name)
    # 保险起见再做一次保序去重
    chain = list(dict.fromkeys(chain))

    if primary_name:
        primary = primary_name.strip().lower()
        if primary in chain:
            chain.remove(primary)
            chain.insert(0, primary)

    return chain


def build_chat_model(
    settings: Settings,
    provider_name: Optional[str] = None,
) -> ChatOpenAI:
    """根据应用配置构建单个 ChatOpenAI 实例（无 fallback）。

    若传入 ``provider_name``，则从 ``settings.providers`` 或
    ``settings.legacy_provider`` 中选择对应 provider；否则使用
    ``settings.default_provider``。

    大多数调用方应改用 :func:`build_chat_model_with_fallback`，仅当确实需要
    "绑死单一 provider，不要任何 failover" 时才使用本函数。

    参数:
        settings: 应用配置对象。
        provider_name: 指定的 provider 名称；为 None 时使用默认 provider。

    返回:
        配置好的 ChatOpenAI 模型实例。
    """
    config = resolve_provider(settings, provider_name)
    if not config.api_key:
        raise ValueError(
            f"Provider {config.name!r} has no API key configured. "
            f"Set {config.name.upper()}_API_KEY in your environment."
        )
    return _build_one(config, settings)


def build_chat_model_with_fallback(
    settings: Settings,
    *,
    primary_name: Optional[str] = None,
) -> Any:
    """构建带 fallback 的聊天模型。

    链顺序由 :func:`_build_chain` 决定：``__legacy__``（如存在）优先，
    其余按 ``ZGRAPH_PROVIDERS`` 声明顺序。

    当某个 provider 抛出 ``_FALLBACK_EXCEPTIONS`` 中的异常时，自动切换到
    链中下一个 provider；其它异常（如 ``BadRequestError``）直接抛出，
    因为它们表示请求本身有问题，fallback 也救不回来。

    链上所有 provider 都失败时，抛出 :class:`AllProvidersFailedError`，
    包含每个 provider 的错误摘要。

    参数:
        settings: 应用配置对象。
        primary_name: 显式指定首选 provider；为 None 时使用链的第一个。

    返回:
        一个 LangChain ``Runnable``，调用方式与 ``ChatOpenAI`` 一致
        （``invoke`` / ``ainvoke`` / ``stream`` 等）。
    """
    chain = _build_chain(settings, primary_name)
    if not chain:
        raise ValueError(
            "Cannot build fallback chain: no providers configured. "
            "Set ZGRAPH_PROVIDERS or BASE_URL+APIKEY."
        )

    # 构建链上每个 provider 对应的 ChatOpenAI，缺 key 的跳过。
    built: list[tuple[str, ChatOpenAI]] = []
    skipped: list[str] = []
    for name in chain:
        try:
            config = resolve_provider(settings, name)
        except ValueError:
            skipped.append(name)
            continue
        if not config.api_key:
            skipped.append(name)
            continue
        built.append((name, _build_one(config, settings)))

    if not built:
        raise ValueError(
            f"Cannot build fallback chain: no provider in chain has an API key. "
            f"Configured: {chain}"
        )

    primary_name_resolved, primary_model = built[0]
    fallbacks = [m for _, m in built[1:]]

    wrapped = primary_model.with_fallbacks(
        fallbacks,
        exceptions_to_handle=_FALLBACK_EXCEPTIONS,
    )

    # 附上链信息便于排障；不破坏 Runnable 接口。
    try:
        wrapped._zgraph_fallback_chain = [name for name, _ in built]  # type: ignore[attr-defined]
        wrapped._zgraph_skipped = skipped  # type: ignore[attr-defined]
        wrapped._zgraph_primary = primary_name_resolved  # type: ignore[attr-defined]
    except Exception:
        pass

    return wrapped


def _build_one(config: ProviderConfig, settings: Settings) -> ChatOpenAI:
    """根据单个 ProviderConfig 构造 ChatOpenAI。

    仅由 :func:`build_chat_model` 和 :func:`build_chat_model_with_fallback` 调用，
    假设 ``config.api_key`` 已非空。
    """
    kwargs: dict[str, Any] = {
        "model": config.model,
        "api_key": config.api_key,
        "base_url": config.base_url,
        "timeout": settings.timeout,
    }
    if settings.temperature is not None:
        kwargs["temperature"] = settings.temperature
    if settings.top_p is not None:
        kwargs["top_p"] = settings.top_p
    if settings.max_tokens is not None:
        kwargs["max_tokens"] = settings.max_tokens
    if settings.reasoning_effort:
        kwargs["reasoning_effort"] = settings.reasoning_effort
    return ChatOpenAI(**kwargs)
