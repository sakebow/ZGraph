from __future__ import annotations

from typing import Any


class CliInputLayer:

    """cli输入层。"""
    name = "input.cli"

    def handle(self, payload: Any) -> str:
        if isinstance(payload, str):
            return payload
        if isinstance(payload, list):
            return " ".join(str(item) for item in payload)
        return str(payload)


class CompletionsInputLayer:

    """completions输入层。"""
    name = "input.completions"

    def handle(
        self,
        payload: dict[str, Any],
        *,
        system_hint: str = "",
    ) -> dict[str, Any]:
        """把 OpenAI 风格请求转换为 runtime 需要的 prompt 字符串。

        参数:
            payload: 原始 HTTP 请求体（dict[str, Any]），至少含 ``messages``。
            system_hint: 可选的系统提示（例如可用 examples 路径列表）。非空时
                会在最前面插入 ``system: ...`` 一行，让 LLM 在拼出来的 prompt
                里最先看到。

        返回:
            含 ``prompt`` / ``model`` / ``stream`` / ``app_id`` / ``raw`` 的字典。
        """
        messages = payload.get("messages")
        if not isinstance(messages, list):
            raise ValueError("OpenAI-compatible request requires messages[]")
        prompt_parts: list[str] = []
        if system_hint:
            prompt_parts.append(f"system: {system_hint}")
        for message in messages:
            role = str(message.get("role", "user"))
            content = message.get("content", "")
            if isinstance(content, list):
                text = " ".join(str(part.get("text", part)) for part in content)
            else:
                text = str(content)
            prompt_parts.append(f"{role}: {text}")
        return {
            "prompt": "\n".join(prompt_parts),
            "model": payload.get("model"),
            "stream": bool(payload.get("stream", False)),
            "app_id": payload.get("app_id") or payload.get("user"),
            "raw": payload,
        }
