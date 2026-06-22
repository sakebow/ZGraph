from __future__ import annotations

from typing import Any
from zgraph.config import Settings
from langchain_openai import ChatOpenAI


def build_chat_model(settings: Settings) -> ChatOpenAI:
    """根据应用配置构建 ChatOpenAI 聊天模型实例。

        参数:
            settings: 应用配置对象，包含模型名称、API 密钥、基础地址等参数（Settings）。

        返回:
            配置好的 ChatOpenAI 模型实例（ChatOpenAI）。
        """
    kwargs: dict[str, Any] = {
        "model": settings.model_name,
        "timeout": settings.timeout,
    }
    if settings.api_key:
        kwargs["api_key"] = settings.api_key
    if settings.base_url:
        kwargs["base_url"] = settings.base_url
    if settings.temperature is not None:
        kwargs["temperature"] = settings.temperature
    if settings.top_p is not None:
        kwargs["top_p"] = settings.top_p
    if settings.max_tokens is not None:
        kwargs["max_tokens"] = settings.max_tokens
    if settings.reasoning_effort:
        kwargs["reasoning_effort"] = settings.reasoning_effort
    return ChatOpenAI(**kwargs)
