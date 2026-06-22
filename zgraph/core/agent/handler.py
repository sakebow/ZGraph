from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from zgraph.core.agent.cancellation import CancellationToken


@dataclass(slots=True)
class AgentHandle:

    """智能体运行句柄，用于标识一次智能体运行并管理其取消与状态。"""
    run_id: str
    cancellation: CancellationToken
    status: str = "created"
    metadata: dict[str, Any] = field(default_factory=dict)

    def stop(self) -> None:
        """触发取消令牌并将运行状态标记为已停止。"""
        self.cancellation.cancel()
        self.status = "stopped"
