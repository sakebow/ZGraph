from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from zgraph.core.agent.cancellation import CancellationToken


class AgentRunner:

    """智能体运行器，负责调用 LangChain 智能体、解析输出并持久化对话记录。"""
    def run(
        self,
        agent: Any,
        user_input: str,
        cancellation: CancellationToken,
        *,
        conversation_path: Path | None = None,
    ) -> str:
        """调用智能体处理用户输入并返回最终输出。
            参数:
                agent: 要调用的 LangChain 智能体对象（Any）。
                user_input: 用户输入的原始文本（str）。
                cancellation: 用于检查取消状态的令牌（CancellationToken）。
                conversation_path: 可选的对话记录保存路径（Path | None）。
            返回:
                智能体生成的文本输出结果（str）。
            """
        cancellation.raise_if_cancelled()
        result = agent.invoke({"messages": [{"role": "user", "content": user_input}]})
        cancellation.raise_if_cancelled()
        messages = result.get("messages") if isinstance(result, dict) else None
        serialized_messages = _serialize_messages(messages)
        if messages:
            last = messages[-1]
            content = getattr(last, "content", None)
            if content is None and isinstance(last, dict):
                content = last.get("content")
            output = str(content or "")
        else:
            output = str(result)
        if conversation_path is not None:
            _write_conversation(
                conversation_path,
                user_input=user_input,
                output=output,
                messages=serialized_messages,
                result=result,
            )
        return output


def _write_conversation(
    path: Path,
    *,
    user_input: str,
    output: str,
    messages: list[dict[str, Any]],
    result: Any,
) -> None:
    """将对话相关信息序列化后写入指定文件。
        参数:
            path: 对话记录文件的保存路径（Path）。
            user_input: 用户输入的原始文本（str）。
            output: 智能体生成的输出文本（str）。
            messages: 序列化后的消息列表（list[dict[str, Any]]）。
            result: 智能体返回的原始结果对象（Any）。
        """
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "created_at": time.time(),
        "user_input": user_input,
        "output": output,
        "message_count": len(messages),
        "messages": messages,
        "raw_result_type": type(result).__name__,
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _serialize_messages(messages: Any) -> list[dict[str, Any]]:
    """将消息列表序列化为可用于 JSON 持久化的字典列表。
        参数:
            messages: 智能体返回的原始消息对象，通常为列表（Any）。
        返回:
            序列化后的消息字典列表（list[dict[str, Any]]）。
        """
    if not isinstance(messages, list):
        return []
    return [_serialize_message(message, index) for index, message in enumerate(messages)]


def _serialize_message(message: Any, index: int) -> dict[str, Any]:
    """将单条消息对象序列化为 JSON 安全的字典。
        参数:
            message: 单条消息对象（Any）。
            index: 消息在列表中的索引位置（int）。
        返回:
            包含消息索引、类型、内容及可选字段的字典（dict[str, Any]）。
        """
    if isinstance(message, dict):
        payload = {str(key): _json_safe(value) for key, value in message.items()}
        payload.setdefault("index", index)
        return payload

    payload: dict[str, Any] = {
        "index": index,
        "type": getattr(message, "type", type(message).__name__),
        "content": _json_safe(getattr(message, "content", "")),
    }
    for attr in (
        "name",
        "id",
        "additional_kwargs",
        "response_metadata",
        "tool_calls",
        "invalid_tool_calls",
        "usage_metadata",
    ):
        value = getattr(message, attr, None)
        if value not in (None, "", [], {}):
            payload[attr] = _json_safe(value)
    return payload


def _json_safe(value: Any) -> Any:
    """将任意值递归转换为 JSON 可序列化的安全格式。
        参数:
            value: 待转换的原始值（Any）。
        返回:
            JSON 可序列化的值；对于无法直接序列化的对象会转换为字符串表示（Any）。
        """
    try:
        json.dumps(value, ensure_ascii=False)
        return value
    except TypeError:
        if isinstance(value, dict):
            return {str(key): _json_safe(item) for key, item in value.items()}
        if isinstance(value, (list, tuple)):
            return [_json_safe(item) for item in value]
        return str(value)
