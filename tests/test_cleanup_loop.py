"""Phase 3.7：后台媒体清理循环 + audit 记录 media 元数据测试。"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest

from zgraph.config import Settings
from zgraph.runtime import ZGraphRuntime
from zgraph.runtime.cleanup_loop import MediaCleanupLoop
from zgraph.runtime.events import ContentDelta, Final, RuntimeEvent
from zgraph.runtime.hooks import RunContext
from zgraph.runtime.hooks.builtin import AuditHook
from zgraph.runtime.media_storage import LocalFSStorage


pytestmark = pytest.mark.integration


def _make_runtime(tmp_settings: Settings) -> ZGraphRuntime:
    """构造一个 media_store 指到 tmp_path 的 Runtime。"""
    return ZGraphRuntime(tmp_settings)


# ---------------------------------------------------------------------------
# AuditHook：记录 media 元数据
# ---------------------------------------------------------------------------


def _ctx(settings: Settings, run_id: str = "r-audit") -> RunContext:
    return RunContext(
        run_id=run_id,
        user_input="hi",
        settings=settings,
        started_at=0.0,
    )


class TestAuditHookMediaRecording:
    async def test_audit_records_media_count_and_records(
        self, tmp_path: Path, tmp_settings: Settings, sample_png: bytes
    ):
        """Final 事件里 RuntimeResult.media 非空时，audit.json 记 media_count + 每条记录。"""
        rt = _make_runtime(tmp_settings)
        image_bytes = sample_png
        rt.emit_media(run_id="r-audit", modality="image", mime="image/png",
                      data=image_bytes, name="audit.png")

        media_events = rt._consume_media("r-audit")
        rt_obj = type(media_events[0])  # MediaReady
        from zgraph.runtime import RuntimeResult

        rt_result = RuntimeResult(
            run_id="r-audit",
            status="completed",
            content="ok",
            media=[m.to_dict() for m in media_events],
        )
        final = Final(run_id="r-audit", status="completed",
                      finish_reason="stop", runtime_result=rt_result)

        audit_file = tmp_path / "audit.json"
        hook = AuditHook(path=audit_file)
        ctx = _ctx(rt.settings, run_id="r-audit")
        await hook(final, ctx)

        assert audit_file.exists()
        record = json.loads(audit_file.read_text(encoding="utf-8").strip())
        assert record["media_count"] == 1
        assert len(record["media"]) == 1
        m = record["media"][0]
        assert m["modality"] == "image"
        assert m["mime"] == "image/png"
        assert m["size_bytes"] == len(image_bytes)
        assert m["expires_at"] != ""
        assert m["url"].endswith("/files/r-audit/audit.png")
        assert m["block_id"].startswith("image-")

    async def test_audit_records_empty_media_list(self, tmp_path: Path, tmp_settings: Settings):
        """没有 media 时，audit 记 media_count=0 + media=[]。"""
        rt = _make_runtime(tmp_settings)
        from zgraph.runtime import RuntimeResult

        rt_result = RuntimeResult(
            run_id="r-empty",
            status="completed",
            content="ok",
            media=[],
        )
        final = Final(run_id="r-empty", status="completed",
                      finish_reason="stop", runtime_result=rt_result)

        audit_file = tmp_path / "audit.json"
        hook = AuditHook(path=audit_file)
        ctx = _ctx(rt.settings, run_id="r-empty")
        await hook(final, ctx)

        record = json.loads(audit_file.read_text(encoding="utf-8").strip())
        assert record["media_count"] == 0
        assert record["media"] == []

    async def test_audit_handles_dict_runtime_result(self, tmp_path: Path, tmp_settings: Settings):
        """兼容性：rt 是 dict 形态时（测试场景），audit 仍然能正确读 media。"""
        rt = _make_runtime(tmp_settings)
        rt_dict = {
            "run_id": "r-dict",
            "status": "completed",
            "content": "ok",
            "reasoning_content": "",
            "interrupt": None,
            "error": None,
            "media": [
                {
                    "block_id": "image-xyz",
                    "modality": "image",
                    "mime": "image/png",
                    "size_bytes": 1024,
                    "expires_at": "2026-07-01T00:00:00Z",
                    "url": "http://x/files/r-dict/a.png",
                }
            ],
        }
        final = Final(run_id="r-dict", status="completed",
                      finish_reason="stop", runtime_result=rt_dict)

        audit_file = tmp_path / "audit.json"
        hook = AuditHook(path=audit_file)
        ctx = _ctx(rt.settings, run_id="r-dict")
        await hook(final, ctx)

        record = json.loads(audit_file.read_text(encoding="utf-8").strip())
        assert record["media_count"] == 1
        assert record["media"][0]["block_id"] == "image-xyz"


# ---------------------------------------------------------------------------
# MediaCleanupLoop
# ---------------------------------------------------------------------------


class TestMediaCleanupLoop:
    def test_disabled_when_interval_zero(self, tmp_settings: Settings):
        """interval <= 0 时 start() 不会创建线程。"""
        rt = _make_runtime(tmp_settings)
        loop = MediaCleanupLoop(interval_seconds=0)
        loop.start(rt)
        assert not loop.is_running
        # stop 也要幂等
        loop.stop()

    def test_start_and_stop_creates_and_joins_thread(self, tmp_settings: Settings):
        """start() 起线程，stop() 优雅退出。"""
        rt = _make_runtime(tmp_settings)
        loop = MediaCleanupLoop(interval_seconds=0.1)  # 100ms 跑一次
        loop.start(rt)
        assert loop.is_running
        # 让线程至少跑一轮
        time.sleep(0.25)
        loop.stop(timeout=1.0)
        assert not loop.is_running

    def test_start_is_idempotent(self, tmp_settings: Settings):
        """多次 start 不会起多个线程。"""
        rt = _make_runtime(tmp_settings)
        loop = MediaCleanupLoop(interval_seconds=60)
        loop.start(rt)
        thread1 = loop._thread
        loop.start(rt)  # 第二次幂等
        thread2 = loop._thread
        assert thread1 is thread2
        loop.stop(timeout=1.0)

    def test_cleanup_actually_deletes_expired_files(
        self, tmp_path: Path, tmp_settings: Settings, sample_png: bytes
    ):
        """循环跑一次后，过期文件被删除，新文件保留。"""
        rt = _make_runtime(tmp_settings)
        image_bytes = sample_png
        rt.emit_media(run_id="r-old", modality="image", mime="image/png",
                      data=image_bytes, name="old.png")
        old_path = tmp_path / "storage" / "r-old" / "old.png"
        # 把它的 mtime 调到很久以前（10 小时前）
        old_time = time.time() - 36000
        os.utime(old_path, (old_time, old_time))

        rt.emit_media(run_id="r-new", modality="image", mime="image/png",
                      data=image_bytes, name="new.png")
        new_path = tmp_path / "storage" / "r-new" / "new.png"

        # TTL 设 1 小时：old 应该被删，new 应该保留
        removed = rt.cleanup_expired_media()
        assert removed == 1
        assert not old_path.exists()
        assert new_path.exists()

    def test_cleanup_loop_calls_runtime_periodically(self, tmp_settings: Settings):
        """loop 启动后能多次触发 cleanup_expired_media。"""
        rt = _make_runtime(tmp_settings)
        # monkey-patch cleanup_expired_media 计数
        original = rt.cleanup_expired_media
        call_count = {"n": 0}

        def counting_cleanup():
            call_count["n"] += 1
            return original()

        rt.cleanup_expired_media = counting_cleanup  # type: ignore[method-assign]

        loop = MediaCleanupLoop(interval_seconds=0.1)
        loop.start(rt)
        # 等 ~350ms，应当至少跑 2 轮
        time.sleep(0.35)
        loop.stop(timeout=1.0)
        assert call_count["n"] >= 2, f"expected >= 2 cleanup calls, got {call_count['n']}"

    def test_cleanup_loop_survives_iteration_exception(self, tmp_settings: Settings):
        """cleanup 抛错时线程不退出，下一轮继续。"""
        rt = _make_runtime(tmp_settings)
        # 让 cleanup_expired_media 一直抛
        def boom():
            raise RuntimeError("simulated cleanup error")

        rt.cleanup_expired_media = boom  # type: ignore[method-assign]

        loop = MediaCleanupLoop(interval_seconds=0.05)
        loop.start(rt)
        time.sleep(0.2)  # 让它跑几轮
        # 线程还活着（没被异常干掉）
        assert loop.is_running
        loop.stop(timeout=1.0)


# ---------------------------------------------------------------------------
# Settings 字段
# ---------------------------------------------------------------------------


class TestSettingsCleanupInterval:
    def test_default_is_300_seconds(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.delenv("ZGRAPH_MEDIA_CLEANUP_INTERVAL_SECONDS", raising=False)
        s = Settings.from_env()
        assert s.media_cleanup_interval_seconds == 300

    def test_env_override(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("ZGRAPH_MEDIA_CLEANUP_INTERVAL_SECONDS", "60")
        s = Settings.from_env()
        assert s.media_cleanup_interval_seconds == 60

    def test_invalid_env_falls_back_to_default(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("ZGRAPH_MEDIA_CLEANUP_INTERVAL_SECONDS", "not-a-number")
        s = Settings.from_env()
        assert s.media_cleanup_interval_seconds == 300

    def test_media_ttl_default(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.delenv("ZGRAPH_MEDIA_TTL_SECONDS", raising=False)
        s = Settings.from_env()
        assert s.media_ttl_seconds == 3600
