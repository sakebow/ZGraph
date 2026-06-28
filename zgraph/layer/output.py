from __future__ import annotations

import json
import time
import uuid
from typing import Any, AsyncIterator, Iterator

from zgraph.runtime.events import (
    ContentDelta,
    Final,
    Interrupt,
    MediaReady,
    ReasoningDelta,
    RuntimeEvent,
    ToolCallArgs,
    ToolCallEnd,
    ToolCallStart,
)


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

    """completions流输出层（旧；基于 fake-stream，把完整字符串按空格切分）。

    新代码请用 :class:`CompletionsAsyncStreamOutputLayer`。
    """

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


class CompletionsAsyncStreamOutputLayer:

    """completions 异步流输出层（Phase 1 新增；基于 RuntimeEvent）。

    把 ``astream()`` 产出的事件流翻译成 OpenAI 兼容的 SSE chunk：

    - ContentDelta → ``delta.content``
    - ReasoningDelta → ``delta.reasoning_content``（DeepSeek / Kimi / MiniMax 兼容字段）
    - ToolCallStart / Args / End → ``delta.tool_calls``
    - MediaReady → ``delta.zgraph_media``
    - Interrupt → ``delta.finish_reason="interrupt"`` + ``delta.zgraph_interrupt``
    - Final → 终止 chunk，含 ``zgraph`` RuntimeResult 字段

    最后追加 ``data: [DONE]\\n\\n``。
    """

    name = "output.completions.stream.async"

    async def astream(
        self,
        events: AsyncIterator[RuntimeEvent],
        *,
        model: str,
    ) -> AsyncIterator[bytes]:
        """消费 RuntimeEvent 流，吐出 SSE 字节流。

        参数:
            events: runtime 产出的事件流（AsyncIterator[RuntimeEvent]）。
            model: OpenAI 响应里回写的 model 字段（str）。

        返回:
            SSE chunk 字节（AsyncIterator[bytes]）。
        """
        completion_id = f"chatcmpl-{uuid.uuid4().hex}"
        created_at = int(time.time())

        chunk_idx = 0
        async for event in events:
            chunk_idx += 1
            event_id = f"{completion_id}-{chunk_idx}"
            event_type = type(event).__name__

            if isinstance(event, ContentDelta):
                yield self._sse(
                    completion_id,
                    created_at,
                    model,
                    {"delta": {"content": event.text}},
                    event_id=event_id,
                    event_type="content_delta",
                )
            elif isinstance(event, ReasoningDelta):
                yield self._sse(
                    completion_id,
                    created_at,
                    model,
                    {"delta": {"reasoning_content": event.text}},
                    event_id=event_id,
                    event_type="reasoning_delta",
                )
            elif isinstance(event, ToolCallStart):
                yield self._sse(
                    completion_id,
                    created_at,
                    model,
                    {
                        "delta": {
                            "role": "assistant",
                            "tool_calls": [
                                {
                                    "index": 0,
                                    "id": event.tool_call_id,
                                    "type": "function",
                                    "function": {
                                        "name": event.tool_name,
                                        "arguments": "",
                                    },
                                }
                            ],
                        }
                    },
                    event_id=event_id,
                    event_type="tool_call_start",
                )
            elif isinstance(event, ToolCallArgs):
                yield self._sse(
                    completion_id,
                    created_at,
                    model,
                    {
                        "delta": {
                            "tool_calls": [
                                {
                                    "index": 0,
                                    "function": {"arguments": event.args_delta},
                                }
                            ]
                        }
                    },
                    event_id=event_id,
                    event_type="tool_call_args",
                )
            elif isinstance(event, ToolCallEnd):
                yield self._sse(
                    completion_id,
                    created_at,
                    model,
                    {
                        "delta": {},
                        "zgraph_tool_end": {
                            "tool_call_id": event.tool_call_id,
                            "tool_name": event.tool_name,
                            "is_error": event.is_error,
                        },
                    },
                    event_id=event_id,
                    event_type="tool_call_end",
                )
            elif isinstance(event, MediaReady):
                yield self._sse(
                    completion_id,
                    created_at,
                    model,
                    {
                        "delta": {
                            "zgraph_media": {
                                "block_id": event.block_id,
                                "modality": event.modality,
                                "mime": event.mime,
                                "url": event.url,
                                "size_bytes": event.size_bytes,
                                "metadata": event.metadata,
                                "expires_at": event.expires_at,
                            }
                        }
                    },
                    event_id=event_id,
                    event_type="media_ready",
                )
            elif isinstance(event, Interrupt):
                yield self._sse(
                    completion_id,
                    created_at,
                    model,
                    {
                        "delta": {},
                        "finish_reason": "interrupt",
                        "zgraph_interrupt": {
                            "run_id": event.run_id,
                            "tool_call_id": event.tool_call_id,
                            "tool_name": event.tool_name,
                            "reason": event.reason,
                            "interrupt_token": event.interrupt_token,
                        },
                    },
                    event_id=event_id,
                    event_type="interrupt",
                )
            elif isinstance(event, Final):
                # 终止块：yield 两个 chunk（finish_reason + zgraph RuntimeResult），
                # 但共享同一 SSE id，让 Last-Event-ID 按 chunk 序号续传时能一次性跳过整块。
                # 客户端靠 ``event:`` 字段（final / final_summary）区分语义。
                final_event_id = event_id
                yield self._sse(
                    completion_id,
                    created_at,
                    model,
                    {"delta": {}, "finish_reason": event.finish_reason},
                    event_id=final_event_id,
                    event_type="final",
                )
                rt = event.runtime_result
                rt_dict = rt.to_dict() if rt is not None else {}
                yield self._sse(
                    completion_id,
                    created_at,
                    model,
                    {"delta": {}, "zgraph": rt_dict},
                    event_id=final_event_id,
                    event_type="final_summary",
                )
                # 结束流
                break

        yield b"data: [DONE]\n\n"

    @staticmethod
    def _sse(
        completion_id: str,
        created_at: int,
        model: str,
        payload: dict[str, Any],
        *,
        event_id: str = "",
        event_type: str = "",
    ) -> bytes:
        """构造一个 SSE data 行（含可选 id: / event: 字段，供断点续传）。"""
        body = {
            "id": completion_id,
            "object": "chat.completion.chunk",
            "created": created_at,
            "model": model,
            "choices": [{"index": 0, **payload}],
        }
        data = f"data: {json.dumps(body, ensure_ascii=False)}\n\n"
        if event_id or event_type:
            prefix = ""
            if event_id:
                prefix += f"id: {event_id}\n"
            if event_type:
                prefix += f"event: {event_type}\n"
            return prefix.encode("utf-8") + data.encode("utf-8")
        return data.encode("utf-8")


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
