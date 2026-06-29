"""Phase 1.5：astream 事件流聚合 + sync 包装测试。

测试目标：
- ``StreamAggregator.collect()`` 把手工注入的 RuntimeEvent 流聚合到 RuntimeResult：
  - ContentDelta 拼接成 content
  - ReasoningDelta 拼接成 reasoning_content（核心验收点）
  - MediaReady 收集成 media 列表
  - Final 事件携带的 hint/intent/capabilities 等其它字段透传
- ``Runtime.run_via_stream()`` 是 ``astream()`` 的同步薄包装 —— 因为 astream
  依赖真实 LangChain agent，这里通过 monkey-patch ``Runtime.astream`` 注入
  预制事件流，避免依赖外部 LLM。
"""

from __future__ import annotations

from typing import AsyncIterator

import pytest

from zgraph.config import Settings
from zgraph.runtime import RuntimeResult, ZGraphRuntime
from zgraph.runtime.events import (
    ContentDelta,
    Final,
    Interrupt,
    MediaReady,
    ReasoningDelta,
    RuntimeEvent,
    ToolCallStart,
)
from zgraph.runtime.stream_aggregator import StreamAggregator


pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# StreamAggregator：手工注入事件流
# ---------------------------------------------------------------------------


class TestAggregatorContentAndReasoning:
    def test_aggregates_content_and_reasoning_from_delta_events(self):
        """手工注入多个 ContentDelta + ReasoningDelta，验证 content / reasoning_content 拼接顺序。"""
        events = [
            ReasoningDelta(text="thinking step 1. "),
            ContentDelta(text="Hello"),
            ReasoningDelta(text="thinking step 2. "),
            ContentDelta(text=" world"),
            Final(
                run_id="r1",
                status="completed",
                finish_reason="stop",
                runtime_result=RuntimeResult(
                    run_id="r1",
                    status="completed",
                    content="",  # 故意空，看是否被聚合值覆盖
                    reasoning_content="",  # 同上
                ),
            ),
        ]
        result = StreamAggregator.collect(events)
        assert result.content == "Hello world"
        assert result.reasoning_content == "thinking step 1. thinking step 2. "
        assert result.status == "completed"

    def test_final_runtime_result_fields_are_preserved(self):
        """Final 事件 RuntimeResult 的 hint / intent / capabilities 透传。"""
        events = [
            ContentDelta(text="ok"),
            Final(
                run_id="r2",
                status="completed",
                finish_reason="stop",
                runtime_result=RuntimeResult(
                    run_id="r2",
                    status="completed",
                    content="",
                    hint={"summary": "test", "domain": "x"},
                    intent={"name": "chat", "confidence": 0.9},
                    capabilities={"risk_level": "low"},
                ),
            ),
        ]
        result = StreamAggregator.collect(events)
        assert result.hint == {"summary": "test", "domain": "x"}
        assert result.intent == {"name": "chat", "confidence": 0.9}
        assert result.capabilities == {"risk_level": "low"}

    def test_no_delta_events_keeps_final_content(self):
        """没有 ContentDelta/ReasoningDelta 时，沿用 Final 里的 content。"""
        events = [
            Final(
                run_id="r3",
                status="completed",
                finish_reason="stop",
                runtime_result=RuntimeResult(
                    run_id="r3",
                    status="completed",
                    content="from-final",
                    reasoning_content="reasoning-from-final",
                ),
            ),
        ]
        result = StreamAggregator.collect(events)
        assert result.content == "from-final"
        assert result.reasoning_content == "reasoning-from-final"


class TestAggregatorMedia:
    def test_aggregates_media_ready_into_media_list(self, tmp_settings: Settings, sample_png: bytes):
        """MediaReady 事件被收集到 RuntimeResult.media 列表里（用真实 PNG 走 emit_media）。"""
        rt = ZGraphRuntime(tmp_settings)
        image_bytes = sample_png

        m1 = rt.emit_media(
            run_id="r-media", modality="image", mime="image/png",
            data=image_bytes, name="a.png",
        )
        m2 = rt.emit_media(
            run_id="r-media", modality="image", mime="image/png",
            data=image_bytes, name="b.png",
        )

        events = [
            ContentDelta(text="hi"),
            m1,
            ContentDelta(text="\n"),
            m2,
            Final(
                run_id="r-media",
                status="completed",
                finish_reason="stop",
                runtime_result=RuntimeResult(
                    run_id="r-media",
                    status="completed",
                    content="",
                ),
            ),
        ]
        result = StreamAggregator.collect(events)
        # 聚合后的 content（被覆盖）
        assert result.content == "hi\n"
        # 两条 media 都被收集
        assert len(result.media) == 2
        urls = [m["url"] for m in result.media]
        assert any(u.endswith("/a.png") for u in urls)
        assert any(u.endswith("/b.png") for u in urls)
        # 每条 media 的 URL 都可访问，bytes 与原图一致
        for m in result.media:
            restored, mime = rt.media_store.open(m["url"])
            assert restored == image_bytes
            assert mime == "image/png"


class TestAggregatorEdgeCases:
    def test_no_final_event_yields_failed_runtime_result(self):
        """没有 Final 事件但有 content → 构造 status=failed 的最小 RuntimeResult。"""
        events = [
            ContentDelta(text="partial output"),
            ReasoningDelta(text="partial thinking"),
        ]
        result = StreamAggregator.collect(events)
        assert result.status == "failed"
        assert result.content == "partial output"
        assert result.reasoning_content == "partial thinking"
        assert result.error == "no Final event"

    def test_empty_event_stream_returns_failed(self):
        """完全没有事件 → RuntimeResult(status=failed, error="empty event stream")。"""
        result = StreamAggregator.collect([])
        assert result.status == "failed"
        assert result.content == ""
        assert result.error == "empty event stream"

    def test_final_with_runtime_result_none_falls_back(self):
        """Final.runtime_result=None 时按"没 Final"处理（构造最小结果）。"""
        events = [
            ContentDelta(text="orphan"),
            Final(
                run_id="r-x",
                status="failed",
                finish_reason="error",
                runtime_result=None,
            ),
        ]
        result = StreamAggregator.collect(events)
        # runtime_result=None 走降级路径
        assert result.error == "no Final event"
        assert result.content == "orphan"


class TestAggregatorInterrupt:
    def test_interrupt_event_propagates_to_final(self):
        """Final 携带 interrupt 时，聚合结果也带 interrupt（透传）。"""
        events = [
            ContentDelta(text="blocked"),
            Final(
                run_id="r-int",
                status="interrupted",
                finish_reason="interrupt",
                runtime_result=RuntimeResult(
                    run_id="r-int",
                    status="interrupted",
                    content="blocked",
                    interrupt={"interrupt_id": "i1", "reason": "high risk"},
                    interrupt_token="tok-1",
                ),
            ),
        ]
        result = StreamAggregator.collect(events)
        assert result.status == "interrupted"
        assert result.interrupt == {"interrupt_id": "i1", "reason": "high risk"}
        assert result.interrupt_token == "tok-1"


# ---------------------------------------------------------------------------
# Runtime.run_via_stream：sync 薄包装
# ---------------------------------------------------------------------------


def _make_runtime_with_mock_astream(
    monkeypatch: pytest.MonkeyPatch,
    tmp_settings: Settings,
    events: list[RuntimeEvent],
) -> ZGraphRuntime:
    """构造一个 Runtime，把 astream() 替换成返回 events 的 async generator。"""
    rt = ZGraphRuntime(tmp_settings)

    async def _fake_astream(user_input: str, *, run_id=None) -> AsyncIterator[RuntimeEvent]:
        for e in events:
            yield e

    monkeypatch.setattr(rt, "astream", _fake_astream)
    return rt


class TestRuntimeRunViaStream:
    def test_sync_wrapper_aggregates_reasoning_content(
        self, monkeypatch: pytest.MonkeyPatch, tmp_settings: Settings
    ):
        """手工注入 ReasoningDelta，验证 run_via_stream() 同步包装能聚合 reasoning_content。

        这是 Phase 1.5 的核心验收点。
        """
        events = [
            ReasoningDelta(text="Let me think. "),
            ContentDelta(text="The answer is 42."),
            ReasoningDelta(text=" Done."),
            Final(
                run_id="r-agg",
                status="completed",
                finish_reason="stop",
                runtime_result=RuntimeResult(
                    run_id="r-agg",
                    status="completed",
                    content="",
                    reasoning_content="",
                ),
            ),
        ]
        rt = _make_runtime_with_mock_astream(monkeypatch, tmp_settings, events)
        result = rt.run_via_stream("test prompt")

        assert result.run_id == "r-agg"
        assert result.status == "completed"
        assert result.content == "The answer is 42."
        assert result.reasoning_content == "Let me think.  Done."  # noqa: E501 — 两段中间是单空格，但 "think. " 和 " Done." 拼接后是两个空格

    def test_sync_wrapper_aggregates_content_and_media(
        self, monkeypatch: pytest.MonkeyPatch, tmp_settings: Settings, sample_png: bytes
    ):
        """run_via_stream() 也聚合 content + media。"""
        rt = ZGraphRuntime(tmp_settings)
        image_bytes = sample_png
        m_event = rt.emit_media(
            run_id="r-stream-media",
            modality="image",
            mime="image/png",
            data=image_bytes,
            name="x.png",
        )

        async def _fake_astream(user_input, *, run_id=None):
            yield ContentDelta(text="here is img:")
            yield m_event
            yield ContentDelta(text=" done")
            yield Final(
                run_id="r-stream-media",
                status="completed",
                finish_reason="stop",
                runtime_result=RuntimeResult(
                    run_id="r-stream-media",
                    status="completed",
                    content="",
                ),
            )

        monkeypatch.setattr(rt, "astream", _fake_astream)

        result = rt.run_via_stream("test")
        assert result.content == "here is img: done"
        assert len(result.media) == 1
        assert result.media[0]["url"].endswith("/x.png")
        # URL 可访问，bytes 一致
        restored, mime = rt.media_store.open(result.media[0]["url"])
        assert restored == image_bytes
        assert mime == "image/png"

    def test_sync_wrapper_preserves_interrupt(
        self, monkeypatch: pytest.MonkeyPatch, tmp_settings: Settings
    ):
        """run_via_stream() 在中断路径下也返回正确的 RuntimeResult。"""
        events = [
            ContentDelta(text="blocked"),
            Final(
                run_id="r-int",
                status="interrupted",
                finish_reason="interrupt",
                runtime_result=RuntimeResult(
                    run_id="r-int",
                    status="interrupted",
                    content="blocked",
                    interrupt={"interrupt_id": "i-1", "reason": "high"},
                    interrupt_token="tok-i",
                ),
            ),
        ]
        rt = _make_runtime_with_mock_astream(monkeypatch, tmp_settings, events)
        result = rt.run_via_stream("test")
        assert result.status == "interrupted"
        assert result.interrupt_token == "tok-i"
        assert result.interrupt == {"interrupt_id": "i-1", "reason": "high"}

    def test_sync_wrapper_propagates_failed_status(
        self, monkeypatch: pytest.MonkeyPatch, tmp_settings: Settings
    ):
        """Final.status=failed 时，run_via_stream() 也返回 failed。"""
        events = [
            ContentDelta(text="partial"),
            Final(
                run_id="r-fail",
                status="failed",
                finish_reason="error",
                runtime_result=RuntimeResult(
                    run_id="r-fail",
                    status="failed",
                    content="",
                    error="boom",
                ),
            ),
        ]
        rt = _make_runtime_with_mock_astream(monkeypatch, tmp_settings, events)
        result = rt.run_via_stream("test")
        assert result.status == "failed"
        assert result.error == "boom"
