"""Phase 3.7：后台媒体清理循环。

行为：
- 启动后每隔 ``cleanup_interval_seconds`` 秒调用一次
  ``runtime.cleanup_expired_media()``，删除早于 ``media_ttl_seconds`` 的媒体。
- 用 threading.Event.wait 做 sleep，不阻塞主事件循环 / 主线程。
- 默认间隔 300 秒（5 分钟）；可通过 ``Settings.media_cleanup_interval_seconds``
  + ``ZGRAPH_MEDIA_CLEANUP_INTERVAL_SECONDS`` 覆盖。
- 提供 ``MediaCleanupLoop.start(runtime)`` / ``MediaCleanupLoop.stop()`` 接口；
  ``main.py::serve()`` 在启动 server 前调用 start，在退出时调用 stop。

设计取舍：
- 用 threading 而不是 asyncio：``main.py::serve()`` 用 ``ThreadingHTTPServer``，
  没有持久 event loop；每请求是 ``asyncio.run(_drain())`` 短生命 loop。引入
  threading 后台线程最干净，不影响 HTTP server 线程模型。
- 一次清理失败仅记 warning，绝不让线程退出 —— 媒体清理是 best-effort 维护性
  任务，不该影响主流程。
"""

from __future__ import annotations

import logging
import threading
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from zgraph.runtime import ZGraphRuntime

logger = logging.getLogger("zgraph.media_cleanup")


class MediaCleanupLoop:
    """Phase 3.7：周期性清理过期媒体。

    参数:
        interval_seconds: 清理间隔（秒）。<= 0 表示禁用循环（不创建线程）。
    """

    def __init__(self, interval_seconds: float) -> None:
        self.interval_seconds = float(interval_seconds)
        self._thread: threading.Thread | None = None
        self._stop_event: threading.Event | None = None

    @property
    def is_running(self) -> bool:
        """后台循环是否在跑。"""
        return self._thread is not None and self._thread.is_alive()

    def _run(self, runtime: "ZGraphRuntime", stop_event: threading.Event) -> None:
        """循环主体。"""
        assert runtime.media_store is not None
        # 第一次跑前先睡一个间隔，避免启动时和首批请求抢 IO
        while not stop_event.is_set():
            # wait() 返回 True 表示被 set，False 表示 timeout
            if stop_event.wait(timeout=self.interval_seconds):
                return
            try:
                removed = runtime.cleanup_expired_media()
                if removed > 0:
                    logger.info("media cleanup removed %d expired file(s)", removed)
            except Exception as exc:
                logger.warning("media cleanup iteration failed: %s", exc)
                # 不退出，继续下一轮

    def start(self, runtime: "ZGraphRuntime") -> None:
        """启动后台清理线程。幂等。"""
        if self.interval_seconds <= 0:
            logger.info("media cleanup loop disabled (interval <= 0)")
            return
        if self.is_running:
            return
        self._stop_event = threading.Event()
        self._thread = threading.Thread(
            target=self._run,
            args=(runtime, self._stop_event),
            name="zgraph-media-cleanup",
            daemon=True,
        )
        self._thread.start()
        logger.info(
            "media cleanup loop started (interval=%ds, ttl=%ds)",
            self.interval_seconds,
            runtime.settings.media_ttl_seconds,
        )

    def stop(self, *, timeout: float = 5.0) -> None:
        """停止后台线程。最长等待 ``timeout`` 秒优雅退出。

        若线程在 timeout 内未结束，**保留 _thread 引用**，避免下一次 start()
        起并发第二个线程；调用方可在稍后重试 stop() 或强制 daemon 退出。
        """
        if not self.is_running:
            return
        assert self._stop_event is not None
        self._stop_event.set()
        assert self._thread is not None
        self._thread.join(timeout=timeout)
        if self._thread.is_alive():
            logger.error(
                "media cleanup loop did not stop within %.1fs; leaving thread ref intact",
                timeout,
            )
            return
        self._thread = None
        self._stop_event = None
        logger.info("media cleanup loop stopped")
