"""媒体流式端到端集成测试。

用一张真实 PNG（来自用户提供的图片）走完完整链路：
1. 读图片字节 → emit_media() → media_store.put()
2. 构造 RuntimeEvent 流（ContentDelta + MediaReady + Final）
3. 过 CompletionsAsyncStreamOutputLayer.astream() → SSE 字节
4. 把 SSE 输出写到 media.log 供审阅
5. 验证：URL 可通过 media_store.open() 拿回字节，且大小一致
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import AsyncIterator

import pytest

from zgraph.config import Settings
from zgraph.layer.output import CompletionsAsyncStreamOutputLayer
from zgraph.runtime import RuntimeResult, ZGraphRuntime
from zgraph.runtime.events import (
    ContentDelta,
    Final,
    MediaReady,
    ReasoningDelta,
    RuntimeEvent,
)


pytestmark = pytest.mark.integration


# 用户提供的真实图片（2.4MB PNG）
SAMPLE_IMAGE = (
    Path(__file__).resolve().parents[1]
    / ".zgraph"
    / "storage"
    / "cfe0b72bebdd40a9a0b5b4a1bc8dc9ea"
    / "140037382_p0.png"
)


def _build_event_stream(
    run_id: str,
    media_event: MediaReady,
    content_text: str = "Here's the image you asked for:",
) -> list[RuntimeEvent]:
    """构造一个 mock 事件流，覆盖 ReasoningDelta → ContentDelta → MediaReady → Final。"""
    return [
        ReasoningDelta(text=f"User wants image for run {run_id}. Generating..."),
        ContentDelta(text=content_text),
        ContentDelta(text="\n\n"),
        media_event,
        ContentDelta(text="\nImage attached."),
        Final(
            run_id=run_id,
            status="completed",
            finish_reason="stop",
            runtime_result=RuntimeResult(
                run_id=run_id,
                status="completed",
                content=content_text + "\n\nImage attached.",
                capabilities={
                    "selected_tools": ["image-gen"],
                    "required_tools": [],
                    "risk_level": "low",
                    "retrieval_strategy": "word",
                },
            ),
        ),
    ]


class TestMediaStreamingPipeline:
    def test_sample_image_exists(self) -> None:
        """前置检查：用户提供的真实 PNG 必须存在。"""
        assert SAMPLE_IMAGE.exists(), f"missing sample image: {SAMPLE_IMAGE}"
        assert SAMPLE_IMAGE.stat().st_size > 0

    async def test_full_pipeline_with_real_image(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """端到端：真实 PNG → media_store → SSE chunk，验证 URL 可访问。"""
        # 把 storage 路径指到 tmp_path，不污染真实目录
        monkeypatch.setenv("ZGRAPH_TMP_STORE_PATH", str(tmp_path / "storage"))
        monkeypatch.setenv("ZGRAPH_STORAGE_PROVIDERS", "localfs")
        monkeypatch.setenv("ZGRAPH_MEDIA_BASE_URL", "http://test.local:9999")

        # 1. 读真实图片字节
        image_bytes = SAMPLE_IMAGE.read_bytes()
        assert len(image_bytes) > 1_000_000, "expected a > 1MB PNG"

        # 2. Runtime 把图存进 media_store
        rt = ZGraphRuntime(Settings.from_env())
        run_id = "media-stream-test-001"
        media_event = rt.emit_media(
            run_id=run_id,
            modality="image",
            mime="image/png",
            data=image_bytes,
            name="anime-girl.png",
            metadata={
                "width": 1512,
                "height": 1912,
                "source": "user-provided",
            },
        )

        # 3. 验证 emit_media 返回的 MediaReady
        assert media_event.block_id.startswith("image-")
        assert media_event.mime == "image/png"
        assert media_event.size_bytes == len(image_bytes)
        assert media_event.url == f"http://test.local:9999/files/{run_id}/anime-girl.png"
        assert media_event.metadata["width"] == 1512

        # 4. 构造事件流 → 过 SSE 层
        events = _build_event_stream(run_id, media_event)

        async def _gen() -> AsyncIterator[RuntimeEvent]:
            for e in events:
                yield e

        chunks: list[bytes] = []
        async for raw in CompletionsAsyncStreamOutputLayer().astream(
            _gen(), model="zgraph"
        ):
            chunks.append(raw)

        # 5. 写 media.log（完整 SSE 输出供审阅）
        log_path = Path(__file__).resolve().parents[1] / "media.log"
        log_path.write_text(
            "".join(c.decode("utf-8") for c in chunks),
            encoding="utf-8",
        )
        assert log_path.exists()
        assert log_path.stat().st_size > 0

        # 6. 验证 SSE 包含 MediaReady 块（URL）
        full_output = b"".join(chunks).decode("utf-8")
        assert "event: media_ready" in full_output
        assert media_event.url in full_output
        assert "/files/media-stream-test-001/anime-girl.png" in full_output
        # reasoning 也得透传
        assert "event: reasoning_delta" in full_output
        assert "User wants image" in full_output
        # content 也得有
        assert "event: content_delta" in full_output
        assert "Image attached" in full_output
        # 收尾 [DONE]
        assert "[DONE]" in full_output

        # 7. 验证图片可被 media_store 取回，且大小一致
        restored_bytes, restored_mime = rt.media_store.open(media_event.url)
        assert restored_bytes == image_bytes, "round-trip bytes mismatch"
        assert restored_mime == "image/png"

    async def test_log_file_shows_event_order(self, tmp_path, monkeypatch) -> None:
        """检查 media.log 的事件顺序：reasoning → content → media_ready → content → final → [DONE]。"""
        monkeypatch.setenv("ZGRAPH_TMP_STORE_PATH", str(tmp_path / "storage"))
        monkeypatch.setenv("ZGRAPH_STORAGE_PROVIDERS", "localfs")
        monkeypatch.setenv("ZGRAPH_MEDIA_BASE_URL", "http://test.local:9999")

        rt = ZGraphRuntime(Settings.from_env())
        image_bytes = b"\x89PNG\r\n\x1a\n" + b"\x00" * 100  # 假 PNG 头
        media_event = rt.emit_media(
            run_id="order-test",
            modality="image",
            mime="image/png",
            data=image_bytes,
            name="t.png",
        )
        events = _build_event_stream("order-test", media_event, content_text="hi")

        async def _gen() -> AsyncIterator[RuntimeEvent]:
            for e in events:
                yield e

        chunks: list[bytes] = []
        async for raw in CompletionsAsyncStreamOutputLayer().astream(_gen(), model="zgraph"):
            chunks.append(raw)

        # 提取每个 SSE event 块（含 id:/event:/data: 前缀）
        raw = b"".join(chunks).decode("utf-8")
        # 按 data: 块切分，提取每个块的 event: 字段
        events_seen: list[str] = []
        current_event = "message"  # SSE 默认 event type
        for line in raw.split("\n"):
            if line.startswith("event: "):
                current_event = line[len("event: "):].strip()
            elif line.startswith("data: ") and line != "data: [DONE]":
                events_seen.append(current_event)
                current_event = "message"
        # 期望顺序（_build_event_stream 里有 5 个非 Final 事件）：
        # reasoning_delta → content_delta ("hi") → content_delta ("\n\n") →
        # media_ready → content_delta ("\nImage attached.") → final → final_summary
        assert events_seen == [
            "reasoning_delta",
            "content_delta",
            "content_delta",
            "media_ready",
            "content_delta",
            "final",
            "final_summary",
        ]
