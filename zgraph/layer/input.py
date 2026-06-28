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

    def handle(self, payload: dict[str, Any]) -> dict[str, Any]:
        messages = payload.get("messages")
        if not isinstance(messages, list):
            raise ValueError("OpenAI-compatible request requires messages[]")
        prompt_parts: list[str] = []
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
