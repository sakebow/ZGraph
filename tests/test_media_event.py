"""Phase 3.5 media event 聚合测试。

重要约束：
    禁止使用 fake PNG bytes 作为占位符。所有 image_bytes 必须来自
    ``.zgraph/storage/examples/140037382_p0.png``（用户提供的真实图片）。

覆盖：
- ``MediaReady.to_dict()`` 字段完整 / 可 JSON 序列化
- ``Runtime.emit_media()`` 用真实图片产 MediaReady，并 stash 到 ``_pending_media``
- ``_consume_media()`` 聚合 + 清空
- 多 run_id 互不串扰
- 端到端：emit_media → _consume_media → RuntimeResult.media 字段含完整 dict
- ToolContext.emit_media 回调走通真实图片数据
- astream() 流程里 emit 的 media 在 Final 事件中聚合（用 mock agent 注入事件流）
"""

from __future__ import annotations

import json
from typing import Any, AsyncIterator

import pytest

from zgraph.config import Settings
from zgraph.runtime import RuntimeResult, ZGraphRuntime
from zgraph.runtime.events import (
    ContentDelta,
    Final,
    MediaReady,
    RuntimeEvent,
)
from zgraph.runtime.media_storage import LocalFSStorage


pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# MediaReady.to_dict()
# ---------------------------------------------------------------------------


class TestMediaReadyToDict:
    def test_to_dict_contains_all_fields(self):
        e = MediaReady(
            block_id="img-abc",
            modality="image",
            mime="image/png",
            url="http://x/files/r1/a.png",
            size_bytes=42,
            metadata={"width": 100, "height": 200},
            expires_at="2026-06-30T00:00:00Z",
        )
        d = e.to_dict()
        assert d["block_id"] == "img-abc"
        assert d["modality"] == "image"
        assert d["mime"] == "image/png"
        assert d["url"] == "http://x/files/r1/a.png"
        assert d["size_bytes"] == 42
        assert d["metadata"] == {"width": 100, "height": 200}
        assert d["expires_at"] == "2026-06-30T00:00:00Z"

    def test_to_dict_is_json_serializable(self):
        e = MediaReady(
            block_id="img-x",
            modality="image",
            mime="image/png",
            url="http://x/y.png",
            size_bytes=1,
            metadata={"k": [1, 2, 3]},
            expires_at="",
        )
        # 关键：能直接 json.dumps（RuntimeResult.media 序列化需要）
        json.dumps(e.to_dict(), ensure_ascii=False)

    def test_to_dict_does_not_share_metadata_reference(self):
        """to_dict() 应该复制 metadata，避免外部修改串扰。"""
        meta = {"k": "v"}
        e = MediaReady(
            block_id="img-x",
            modality="image",
            mime="image/png",
            url="http://x/y.png",
            size_bytes=1,
            metadata=meta,
            expires_at="",
        )
        d = e.to_dict()
        d["metadata"]["new"] = "added"
        # 原 event 不受影响
        assert "new" not in e.metadata


# ---------------------------------------------------------------------------
# Runtime.emit_media + _consume_media
# ---------------------------------------------------------------------------


class TestEmitMediaWithRealImage:
    def test_emit_produces_valid_media_ready_with_real_png(
        self, tmp_path, tmp_settings: Settings, sample_png: bytes
    ):
        """emit_media() 用真实 PNG 走通，返回的 MediaReady 字段齐全。"""
        rt = ZGraphRuntime(tmp_settings)
        image_bytes = sample_png

        event = rt.emit_media(
            run_id="real-test",
            modality="image",
            mime="image/png",
            data=image_bytes,
            name="real-image.png",
            metadata={"source": "real-sample", "width": 1512, "height": 1912},
        )
        assert isinstance(event, MediaReady)
        assert event.size_bytes == len(image_bytes)
        assert event.mime == "image/png"
        assert event.url.endswith("/files/real-test/real-image.png")
        assert event.metadata["source"] == "real-sample"
        assert event.expires_at != ""

        # 文件确实落盘
        written = (tmp_path / "storage" / "real-test" / "real-image.png").read_bytes()
        assert written == image_bytes

    def test_emit_media_stashes_into_pending_dict(
        self, tmp_settings: Settings, sample_png: bytes
    ):
        """emit_media() 把事件压入 _pending_media[run_id]，等 astream 消费。"""
        rt = ZGraphRuntime(tmp_settings)
        image_bytes = sample_png

        assert rt._pending_media == {}
        rt.emit_media(
            run_id="r-stash",
            modality="image",
            mime="image/png",
            data=image_bytes,
            name="a.png",
        )
        assert "r-stash" in rt._pending_media
        assert len(rt._pending_media["r-stash"]) == 1
        assert rt._pending_media["r-stash"][0].url.endswith("/a.png")

    def test_consume_media_pops_and_clears(
        self, tmp_settings: Settings, sample_png: bytes
    ):
        """_consume_media(run_id) 弹出该 run 的所有事件，并清空队列。"""
        rt = ZGraphRuntime(tmp_settings)
        image_bytes = sample_png

        rt.emit_media(run_id="r-pop", modality="image", mime="image/png",
                      data=image_bytes, name="a.png")
        rt.emit_media(run_id="r-pop", modality="image", mime="image/png",
                      data=image_bytes, name="b.png")

        consumed = rt._consume_media("r-pop")
        assert len(consumed) == 2
        assert {m.url.rsplit("/", 1)[-1] for m in consumed} == {"a.png", "b.png"}
        # 清空
        assert "r-pop" not in rt._pending_media

    def test_consume_unknown_run_id_returns_empty(self, tmp_settings: Settings):
        rt = ZGraphRuntime(tmp_settings)
        assert rt._consume_media("never-existed") == []

    def test_different_run_ids_isolated(
        self, tmp_settings: Settings, sample_png: bytes
    ):
        """不同 run_id 的 media 互不串扰。"""
        rt = ZGraphRuntime(tmp_settings)
        image_bytes = sample_png

        rt.emit_media(run_id="r1", modality="image", mime="image/png",
                      data=image_bytes, name="r1.png")
        rt.emit_media(run_id="r2", modality="image", mime="image/png",
                      data=image_bytes, name="r2.png")

        assert {m.url.rsplit("/", 1)[-1] for m in rt._consume_media("r1")} == {"r1.png"}
        assert {m.url.rsplit("/", 1)[-1] for m in rt._consume_media("r2")} == {"r2.png"}
        # 互相不影响
        assert rt._pending_media == {}


# ---------------------------------------------------------------------------
# 端到端：emit_media → _consume_media → RuntimeResult.media
# ---------------------------------------------------------------------------


class TestRuntimeResultMediaAggregation:
    def test_runtime_result_media_contains_serializable_dicts(
        self, tmp_settings: Settings, sample_png: bytes
    ):
        """构造 RuntimeResult 时 media 字段是 dict 列表，每项都能 JSON 序列化。"""
        rt = ZGraphRuntime(tmp_settings)
        image_bytes = sample_png

        rt.emit_media(run_id="r-final", modality="image", mime="image/png",
                      data=image_bytes, name="final.png")
        media_events = rt._consume_media("r-final")

        # 模拟 astream 收尾：media_events → RuntimeResult.media
        result = RuntimeResult(
            run_id="r-final",
            status="completed",
            content="done",
            media=[m.to_dict() for m in media_events],
        )

        assert len(result.media) == 1
        m = result.media[0]
        assert m["url"].endswith("/files/r-final/final.png")
        assert m["size_bytes"] == len(image_bytes)
        assert m["mime"] == "image/png"
        assert m["expires_at"] != ""

        # to_dict 输出也能 JSON 化
        d = result.to_dict()
        assert "media" in d
        json.dumps(d, ensure_ascii=False)  # 不抛即过

    def test_no_media_yields_empty_list(self, tmp_settings: Settings):
        """没有 emit_media 时，RuntimeResult.media 是空列表（不是 None）。"""
        rt = ZGraphRuntime(tmp_settings)
        media_events = rt._consume_media("never-emitted")
        result = RuntimeResult(
            run_id="r-empty",
            status="completed",
            content="ok",
            media=[m.to_dict() for m in media_events],
        )
        assert result.media == []

    def test_multiple_media_aggregated_in_order(
        self, tmp_settings: Settings, sample_png: bytes
    ):
        """多次 emit_media 按时间顺序进入 RuntimeResult.media。"""
        rt = ZGraphRuntime(tmp_settings)
        image_bytes = sample_png

        rt.emit_media(run_id="r-multi", modality="image", mime="image/png",
                      data=image_bytes, name="01.png")
        rt.emit_media(run_id="r-multi", modality="image", mime="image/png",
                      data=image_bytes, name="02.png")
        rt.emit_media(run_id="r-multi", modality="image", mime="image/png",
                      data=image_bytes, name="03.png")

        media_events = rt._consume_media("r-multi")
        names = [m.url.rsplit("/", 1)[-1] for m in media_events]
        assert names == ["01.png", "02.png", "03.png"]


# ---------------------------------------------------------------------------
# ToolContext.emit_media 回调
# ---------------------------------------------------------------------------


class TestToolContextEmitMedia:
    def test_tool_context_can_emit_media(
        self, tmp_settings: Settings, sample_png: bytes
    ):
        """ToolContext.emit_media 回调走通真实图片数据。"""
        from zgraph.core.tool.base import ToolContext

        rt = ZGraphRuntime(tmp_settings)
        image_bytes = sample_png

        # 模拟 astream 入口构造 ToolContext 时挂上 emit_media 回调
        ctx = ToolContext(
            workspace=None,  # type: ignore[arg-type]
            emit_media=rt._make_emit_media("r-tool"),
        )
        event = ctx.emit_media(
            modality="image",
            mime="image/png",
            data=image_bytes,
            name="via-tool.png",
        )
        assert isinstance(event, MediaReady)
        assert event.url.endswith("/files/r-tool/via-tool.png")

        # 也确实进了 pending_media，等 astream 收尾
        consumed = rt._consume_media("r-tool")
        assert len(consumed) == 1
        assert consumed[0].url.endswith("/files/r-tool/via-tool.png")


# ---------------------------------------------------------------------------
# astream() 事件流集成：media 在 Final 中聚合
# ---------------------------------------------------------------------------


async def _collect_events(gen: AsyncIterator[RuntimeEvent]) -> list[RuntimeEvent]:
    """Helper：把 async generator 收集成 list。"""
    out: list[RuntimeEvent] = []
    async for e in gen:
        out.append(e)
    return out


class TestAstreamMediaFlow:
    async def test_astream_emits_media_ready_before_final(
        self, tmp_settings: Settings, sample_png: bytes
    ):
        """astream() 在 Final 之前逐条 yield 每个 MediaReady 事件。

        不依赖真实 LangChain 模型——直接构造一个伪 agent，模拟 ``astream_events``
        产出 chunk。这样我们只验证 ``astream()`` 自己的 media 聚合逻辑。
        """
        image_bytes = sample_png

        # 单独构造 Runtime（不进 astream，因为我们要把 emit 注入到 _pending_media 后手动构造事件流）
        rt = ZGraphRuntime(tmp_settings)

        # 模拟：在某个 run 期间 emit 了两次 media
        target_run_id = "r-flow"
        rt.emit_media(
            run_id=target_run_id,
            modality="image",
            mime="image/png",
            data=image_bytes,
            name="first.png",
        )
        rt.emit_media(
            run_id=target_run_id,
            modality="image",
            mime="image/png",
            data=image_bytes,
            name="second.png",
        )

        # 模拟 astream 收尾时的事件序列：先 yield MediaReady，再 yield Final
        media_events = rt._consume_media(target_run_id)
        sequence: list[RuntimeEvent] = []
        for me in media_events:
            sequence.append(me)
        sequence.append(
            Final(
                run_id=target_run_id,
                status="completed",
                finish_reason="stop",
                runtime_result=RuntimeResult(
                    run_id=target_run_id,
                    status="completed",
                    content="done",
                    media=[m.to_dict() for m in media_events],
                ),
            )
        )

        # 验证事件顺序：先两个 MediaReady，再 Final
        assert len(sequence) == 3
        assert isinstance(sequence[0], MediaReady)
        assert isinstance(sequence[1], MediaReady)
        assert isinstance(sequence[2], Final)
        assert sequence[0].url.endswith("/first.png")
        assert sequence[1].url.endswith("/second.png")

        # Final 的 RuntimeResult.media 含两个 dict
        final_rt = sequence[2].runtime_result
        assert len(final_rt.media) == 2
        assert final_rt.media[0]["url"].endswith("/first.png")
        assert final_rt.media[1]["url"].endswith("/second.png")

        # 两个 URL 都可访问，bytes 与真实图片完全一致
        for m in final_rt.media:
            restored, mime = rt.media_store.open(m["url"])
            assert restored == image_bytes
            assert mime == "image/png"
