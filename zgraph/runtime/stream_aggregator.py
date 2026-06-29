"""Phase 1.5：RuntimeEvent 流聚合器。

职责：
- ``StreamAggregator.collect(events)`` 把 ``astream()`` 产出的事件流聚合成
  ``RuntimeResult``。这是 ``Runtime.run_via_stream()`` 同步薄包装的核心逻辑。
- 聚合规则：
  - ``ContentDelta`` → 拼接成 ``content``
  - ``ReasoningDelta`` → 拼接成 ``reasoning_content``（thinking 模型专用）
  - ``MediaReady`` → 收集成 ``media`` 列表
  - 最后一个 ``Final`` 事件 → 携带其余字段（hint/intent/capabilities/...）；
    如果没有 Final 事件，构造一个 ``status=failed`` 的最小 RuntimeResult。
- 聚合后的 content/reasoning_content 覆盖 Final 事件里 RuntimeResult 同名字段
  （以事件流为准，避免双源不一致）。
- 如果完全没有 Final 事件，但有 content/reasoning，构造最小 RuntimeResult
  并设 ``error="no Final event"``，便于上层做降级处理。

设计取舍：
- ``collect()`` 是纯函数（输入 Iterable[RuntimeEvent]，输出 RuntimeResult），
  不依赖 Runtime 实例 —— 这样可以脱离真实 LangChain agent 直接单元测试。
"""

from __future__ import annotations

from typing import Iterable

from zgraph.runtime import RuntimeResult
from zgraph.runtime.events import (
    ContentDelta,
    Final,
    MediaReady,
    ReasoningDelta,
    RuntimeEvent,
)


class StreamAggregator:
    """Phase 1.5：RuntimeEvent 流 → RuntimeResult 聚合器。"""

    @staticmethod
    def collect(events: Iterable[RuntimeEvent]) -> RuntimeResult:
        """从事件流构造 RuntimeResult。

        参数:
            events: ``astream()`` 产出的事件流（任意可迭代）。

        返回:
            聚合后的 RuntimeResult。
        """
        content_parts: list[str] = []
        reasoning_parts: list[str] = []
        media_events: list[MediaReady] = []
        final_event: Final | None = None

        for event in events:
            if isinstance(event, ContentDelta):
                content_parts.append(event.text)
            elif isinstance(event, ReasoningDelta):
                reasoning_parts.append(event.text)
            elif isinstance(event, MediaReady):
                media_events.append(event)
            elif isinstance(event, Final):
                final_event = event

        aggregated_content = "".join(content_parts)
        aggregated_reasoning = "".join(reasoning_parts)
        aggregated_media_dicts = [m.to_dict() for m in media_events]

        # 有 Final：用 Final 携带的 RuntimeResult 作底，用聚合值覆盖 content /
        # reasoning_content / media（事件流才是真实数据源）。
        if final_event is not None and final_event.runtime_result is not None:
            rt = final_event.runtime_result
            return RuntimeResult(
                run_id=rt.run_id,
                status=rt.status,
                content=aggregated_content if content_parts else rt.content,
                hint=dict(rt.hint or {}),
                intent=dict(rt.intent or {}),
                todo=list(rt.todo or []),
                capabilities=dict(rt.capabilities or {}),
                interrupt=rt.interrupt,
                artifacts=list(rt.artifacts or []),
                error=rt.error,
                data=rt.data,
                reasoning_content=(
                    aggregated_reasoning if reasoning_parts else rt.reasoning_content
                ),
                media=aggregated_media_dicts if media_events else list(rt.media or []),
                interrupt_token=rt.interrupt_token,
            )

        # 没 Final 但有聚合内容：构造最小 RuntimeResult，标记 error 便于诊断
        if aggregated_content or aggregated_reasoning or media_events:
            return RuntimeResult(
                run_id="",
                status="failed",
                content=aggregated_content,
                reasoning_content=aggregated_reasoning,
                media=aggregated_media_dicts,
                error="no Final event",
            )

        # 完全没有事件
        return RuntimeResult(
            run_id="",
            status="failed",
            content="",
            error="empty event stream",
        )
