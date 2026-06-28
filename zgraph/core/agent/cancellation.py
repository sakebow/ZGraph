from __future__ import annotations

from dataclasses import dataclass
from threading import Event


@dataclass(slots=True)
class CancellationToken:

    """取消令牌，用于控制并检查异步或长时间运行任务的取消状态。"""
    _event: Event

    @classmethod
    def create(cls) -> "CancellationToken":
        """创建一个新的取消令牌实例。
            返回:
                初始化完成的 CancellationToken 实例（CancellationToken）。
            """
        return cls(Event())

    def cancel(self) -> None:
        """触发取消信号，将令牌标记为已取消。"""
        self._event.set()

    @property
    def cancelled(self) -> bool:
        """判断当前令牌是否已被取消。"""
        return self._event.is_set()

    def raise_if_cancelled(self) -> None:
        """如果令牌已被取消，则抛出运行时异常。"""
        if self.cancelled:
            raise RuntimeError("Agent run was cancelled")
