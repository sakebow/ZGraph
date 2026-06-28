from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Optional


@dataclass
class RuntimeEvent:
    """所有 runtime 流式事件的基类。

    Runtime.astream() 异步产出这些事件；HTTP / CLI / hook 链路消费它们。
    """


# —— 文本域（高频、纯文本） ——


@dataclass
class ContentDelta(RuntimeEvent):
    """模型正文增量。"""

    text: str


@dataclass
class ReasoningDelta(RuntimeEvent):
    """思考过程增量（thinking 模型）。从 AIMessageChunk.additional_kwargs['reasoning_content'] 提取。"""

    text: str


# —— 工具调用 ——


@dataclass
class ToolCallStart(RuntimeEvent):
    """工具调用开始。"""

    tool_call_id: str
    tool_name: str


@dataclass
class ToolCallArgs(RuntimeEvent):
    """工具调用参数增量（JSON 字符串片段）。"""

    tool_call_id: str
    args_delta: str


@dataclass
class ToolCallEnd(RuntimeEvent):
    """工具调用结束。"""

    tool_call_id: str
    tool_name: str
    result: Any = None
    is_error: bool = False


# —— 媒体（URL 模式：ready 才 emit 一次） ——


@dataclass
class MediaReady(RuntimeEvent):
    """媒体已生成并上传到 storage，返回可访问 URL。

    URL 默认 TTL 1h（可配）。MediaStore 在 put 时填 expires_at。
    """

    block_id: str
    modality: str  # image | audio | video | file
    mime: str
    url: str
    size_bytes: int
    metadata: dict[str, Any] = field(default_factory=dict)
    expires_at: str = ""


# —— 流控 ——


@dataclass
class Interrupt(RuntimeEvent):
    """Guardian 中断：客户端需要人工批准才能继续。"""

    run_id: str
    tool_call_id: str
    tool_name: str
    reason: str
    interrupt_token: str = ""  # 用于后续 /resume 鉴权


@dataclass
class Final(RuntimeEvent):
    """流的最后一个事件。一定会 emit；携带完整 RuntimeResult 汇总。"""

    run_id: str
    status: Literal["completed", "failed", "interrupted"]
    finish_reason: str
    usage: dict[str, Any] = field(default_factory=dict)
    runtime_result: Optional[Any] = None  # RuntimeResult；用 Any 避免循环导入
