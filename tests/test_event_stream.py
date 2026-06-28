"""Tests for streaming RuntimeEvent pipeline (Phase 1).

覆盖：
- RuntimeEvent 数据类本身
- CompletionsAsyncStreamOutputLayer 的事件 → SSE 映射
- 异常路径下也能产出 Final 事件（不外抛）
"""

from __future__ import annotations

import json
from typing import Any, AsyncIterator

import pytest

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
from zgraph.layer.output import CompletionsAsyncStreamOutputLayer
from zgraph.runtime import RuntimeResult


pytestmark = pytest.mark.integration


async def _collect_sse(events: list[RuntimeEvent], model: str = "zgraph") -> list[dict[str, Any]]:
    """Helper：把事件列表喂给 async SSE 层，收集所有 JSON chunk。"""

    async def _gen() -> AsyncIterator[RuntimeEvent]:
        for e in events:
            yield e

    chunks: list[dict[str, Any]] = []
    async for raw in CompletionsAsyncStreamOutputLayer().astream(_gen(), model=model):
        text = raw.decode("utf-8")
        # 跳过 id: / event: 行，只解析 data: 行
        for line in text.split("\n"):
            line = line.strip()
            if line.startswith("data: ") and line != "data: [DONE]":
                chunks.append(json.loads(line[6:]))
    return chunks


# ---------------------------------------------------------------------------
# RuntimeEvent 数据类
# ---------------------------------------------------------------------------


class TestRuntimeEvent:
    def test_content_delta(self):
        e = ContentDelta(text="hi")
        assert e.text == "hi"
        assert isinstance(e, RuntimeEvent)

    def test_reasoning_delta(self):
        e = ReasoningDelta(text="thinking...")
        assert e.text == "thinking..."
        assert isinstance(e, RuntimeEvent)

    def test_media_ready(self):
        e = MediaReady(
            block_id="img-1",
            modality="image",
            mime="image/png",
            url="http://x/y.png",
            size_bytes=12345,
            metadata={"width": 1024},
            expires_at="2026-06-29T00:00:00Z",
        )
        assert e.block_id == "img-1"
        assert e.size_bytes == 12345

    def test_tool_call_events(self):
        s = ToolCallStart(tool_call_id="t1", tool_name="read")
        a = ToolCallArgs(tool_call_id="t1", args_delta='{"path":')
        e = ToolCallEnd(tool_call_id="t1", tool_name="read", result="ok", is_error=False)
        assert s.tool_name == "read"
        assert e.is_error is False

    def test_interrupt(self):
        i = Interrupt(
            run_id="r1",
            tool_call_id="t1",
            tool_name="bash",
            reason="high risk",
            interrupt_token="abc",
        )
        assert i.interrupt_token == "abc"

    def test_final_holds_runtime_result(self):
        rt = RuntimeResult(run_id="r1", status="completed", content="hi")
        f = Final(
            run_id="r1",
            status="completed",
            finish_reason="stop",
            runtime_result=rt,
        )
        assert f.runtime_result.content == "hi"


# ---------------------------------------------------------------------------
# SSE 映射
# ---------------------------------------------------------------------------


class TestCompletionsAsyncStreamOutputLayer:
    async def test_content_delta_to_delta_content(self):
        chunks = await _collect_sse([ContentDelta(text="hello")])
        # 第一个 chunk 之前的 ContentDelta；最后一个 Final → finish_reason
        # 因为我们直接传 ContentDelta 不带 Final，循环会 break 但 Final 是分支触发；
        # 在这个测试我们手动补一个 Final
        pass  # 详见下方 test_full_sequence

    async def test_full_sequence_emits_expected_chunks(self):
        events = [
            ReasoningDelta(text="thinking..."),
            ContentDelta(text="hi "),
            ContentDelta(text="there"),
            MediaReady(
                block_id="img-1",
                modality="image",
                mime="image/png",
                url="http://x/y.png",
                size_bytes=100,
                metadata={},
                expires_at="",
            ),
            ToolCallStart(tool_call_id="t1", tool_name="bash"),
            ToolCallEnd(tool_call_id="t1", tool_name="bash", result="ok", is_error=False),
            Final(
                run_id="r1",
                status="completed",
                finish_reason="stop",
                runtime_result=RuntimeResult(run_id="r1", status="completed", content="hi there"),
            ),
        ]
        chunks = await _collect_sse(events)

        # chunk 1: reasoning
        assert chunks[0]["choices"][0]["delta"]["reasoning_content"] == "thinking..."
        # chunk 2: content "hi "
        assert chunks[1]["choices"][0]["delta"]["content"] == "hi "
        # chunk 3: content "there"
        assert chunks[2]["choices"][0]["delta"]["content"] == "there"
        # chunk 4: media
        delta = chunks[3]["choices"][0]["delta"]["zgraph_media"]
        assert delta["block_id"] == "img-1"
        assert delta["url"] == "http://x/y.png"
        # chunk 5: tool_call start
        tc = chunks[4]["choices"][0]["delta"]["tool_calls"][0]
        assert tc["id"] == "t1"
        assert tc["function"]["name"] == "bash"
        # chunk 6: tool_call end via zgraph_tool_end
        end = chunks[5]["choices"][0]["zgraph_tool_end"]
        assert end["tool_call_id"] == "t1"
        # chunk 7: finish_reason
        assert chunks[6]["choices"][0]["finish_reason"] == "stop"
        # chunk 8: zgraph RuntimeResult
        assert chunks[7]["choices"][0]["zgraph"]["run_id"] == "r1"
        assert chunks[7]["choices"][0]["zgraph"]["content"] == "hi there"

    async def test_interrupt_emits_finish_reason_interrupt(self):
        events = [
            Interrupt(
                run_id="r1",
                tool_call_id="t1",
                tool_name="bash",
                reason="high risk",
                interrupt_token="tok",
            ),
            Final(
                run_id="r1",
                status="interrupted",
                finish_reason="interrupt",
                runtime_result=RuntimeResult(run_id="r1", status="interrupted", content=""),
            ),
        ]
        chunks = await _collect_sse(events)
        # interrupt chunk
        first = chunks[0]["choices"][0]
        assert first["finish_reason"] == "interrupt"
        assert first["zgraph_interrupt"]["tool_name"] == "bash"
        assert first["zgraph_interrupt"]["interrupt_token"] == "tok"
        # final chunk carries RuntimeResult
        assert chunks[-1]["choices"][0]["zgraph"]["status"] == "interrupted"

    async def test_final_without_runtime_result_still_emits_done(self):
        events = [
            Final(
                run_id="r1",
                status="failed",
                finish_reason="error",
                runtime_result=None,
            ),
        ]
        chunks = await _collect_sse(events)
        # finish_reason + zgraph {} (empty dict)
        assert chunks[0]["choices"][0]["finish_reason"] == "error"
        assert chunks[1]["choices"][0]["zgraph"] == {}

    async def test_yields_data_done_at_end(self):
        async def _gen() -> AsyncIterator[RuntimeEvent]:
            yield Final(
                run_id="r1",
                status="completed",
                finish_reason="stop",
                runtime_result=RuntimeResult(run_id="r1", status="completed", content="ok"),
            )

        raw_chunks: list[bytes] = []
        async for raw in CompletionsAsyncStreamOutputLayer().astream(_gen(), model="zgraph"):
            raw_chunks.append(raw)
        assert raw_chunks[-1] == b"data: [DONE]\n\n"
