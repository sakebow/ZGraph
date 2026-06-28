"""RuntimeHook 体系（Phase 2）。

设计：
- RuntimeHook 是 callable，签名 ``async def __call__(event, ctx) -> RuntimeEvent | None``
- 可以在事件流上观察、修改、或丢弃（return None）事件
- 异常隔离：单个 hook 抛错不会影响主流程
- 默认钩子（AuditHook / MetricsHook / PIIFilterHook）随 Runtime 一同注册
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from zgraph.config import Settings

if TYPE_CHECKING:
    from zgraph.runtime import RuntimeResult
    from zgraph.runtime.events import RuntimeEvent


@dataclass
class RunContext:
    """per-run 的状态，hooks 之间共享。

    字段:
        run_id: 本次运行的唯一标识符（str）。
        user_input: 用户输入的原始文本（str）。
        settings: 全局配置（Settings）。
        started_at: 启动时间戳（float，time.time()）。
        metadata: 跨 hook 共享的临时存储（dict[str, Any]）。下游 hook 可读
            上游 hook 写入的键值对；典型用途：MetricsHook 累计 token 数，
            AuditHook 把它写进 audit.json。
    """

    run_id: str
    user_input: str
    settings: Settings
    started_at: float
    metadata: dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class RuntimeHook(Protocol):
    """事件流上的中间件。

    实现约定：
    - 签名 ``async def __call__(event: RuntimeEvent, ctx: RunContext) -> RuntimeEvent | None``
    - return event = 透传（可修改 event 的字段）
    - return None = drop 这个事件（后续 hook 和 yield 都不会看到它）
    - 抛异常 = Runtime 捕获、log error、跳过该 hook、继续流（不破坏主流程）
    """

    async def __call__(
        self, event: "RuntimeEvent", ctx: RunContext
    ) -> "RuntimeEvent | None":
        ...
