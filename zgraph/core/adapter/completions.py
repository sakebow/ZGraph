from __future__ import annotations

from typing import Any


class CompletionsAdapter:

    """completions适配器。"""
    def parse(self, payload: dict[str, Any]) -> str:
        messages = payload.get("messages") or []
        if not messages:
            return str(payload.get("prompt") or "")
        parts: list[str] = []
        for message in messages:
            role = message.get("role", "user")
            content = message.get("content", "")
            if isinstance(content, list):
                text = " ".join(str(item.get("text", item)) for item in content)
            else:
                text = str(content)
            parts.append(f"{role}: {text}")
        return "\n".join(parts)

    def format(self, content: str, *, metadata: dict[str, Any] | None = None) -> dict[str, Any]:
        import time
        import uuid
        return {
            "id": f"chatcmpl-{uuid.uuid4().hex}",
            "object": "chat.completion",
            "created": int(time.time()),
            "model": (metadata or {}).get("model", "zgraph"),
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": content},
                    "finish_reason": "stop",
                }
            ],
            "usage": {
                "prompt_tokens": 0,
                "completion_tokens": len(content.split()),
                "total_tokens": len(content.split()),
            },
        }
