from __future__ import annotations

import json
import time
import uuid
from typing import Any, Iterator


def _completion_envelope(content: str, model: str) -> dict[str, Any]:
    """内部方法：completionenvelope。
    
        参数:
            content: content（str）
            model: 模型（str）
    
        返回:
            返回类型为 dict[str, Any] 的结果
        """

    return {
        "id": f"chatcmpl-{uuid.uuid4().hex}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model,
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


class CompletionsGenerateOutputLayer:

    """completions生成输出层。"""
    name = "output.completions.generate"

    def handle(self, payload: dict[str, Any]) -> dict[str, Any]:
        """处理。
        
            参数:
                payload: 载荷（dict[str, Any]）
        
            返回:
                返回类型为 dict[str, Any] 的结果
            """

        return _completion_envelope(str(payload.get("content", "")), str(payload.get("model", "zgraph")))


class CompletionsStreamOutputLayer:

    """completions流输出层。"""
    name = "output.completions.stream"

    def handle(self, payload: dict[str, Any]) -> Iterator[bytes]:
        content = str(payload.get("content", ""))
        model = str(payload.get("model", "zgraph"))
        completion_id = f"chatcmpl-{uuid.uuid4().hex}"
        for token in content.split(" "):
            chunk = {
                "id": completion_id,
                "object": "chat.completion.chunk",
                "created": int(time.time()),
                "model": model,
                "choices": [{"index": 0, "delta": {"content": token + " "}, "finish_reason": None}],
            }
            yield f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n".encode("utf-8")
        done = {
            "id": completion_id,
            "object": "chat.completion.chunk",
            "created": int(time.time()),
            "model": model,
            "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
        }
        yield f"data: {json.dumps(done, ensure_ascii=False)}\n\n".encode("utf-8")
        yield b"data: [DONE]\n\n"


class CliGenerateOutputLayer:

    """cli生成输出层。"""
    name = "output.cli.generate"

    def handle(self, payload: dict[str, Any]) -> str:
        return str(payload.get("content", ""))


class CliStreamOutputLayer:

    """cli流输出层。"""
    name = "output.cli.stream"

    def handle(self, payload: dict[str, Any]) -> Iterator[str]:
        content = str(payload.get("content", ""))
        for token in content.split(" "):
            yield token + " "
