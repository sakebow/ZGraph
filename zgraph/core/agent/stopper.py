from __future__ import annotations

from zgraph.core.agent.handler import AgentHandle


class AgentStopper:

    """智能体停止器，用于统一封装停止智能体运行的操作。"""
    def stop(self, handle: AgentHandle) -> None:
        """停止指定句柄所代表智能体运行。
            参数:
                handle: 要停止的智能体运行句柄（AgentHandle）。
            """
        handle.stop()
