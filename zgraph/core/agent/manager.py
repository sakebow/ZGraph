from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from pathlib import Path

from zgraph.config import Settings
from zgraph.core.agent.cancellation import CancellationToken
from zgraph.core.agent.factory import AgentFactory
from zgraph.core.agent.handler import AgentHandle
from zgraph.core.agent.runner import AgentResult, AgentRunner
from zgraph.core.agent.stopper import AgentStopper
from zgraph.core.tool.base import RuntimeTool


@dataclass(slots=True)
class AgentManager:

    """智能体管理器，负责协调智能体的创建、运行、停止及生命周期状态管理。"""
    settings: Settings
    factory: AgentFactory = field(init=False)
    runner: AgentRunner = field(default_factory=AgentRunner)
    stopper: AgentStopper = field(default_factory=AgentStopper)
    handles: dict[str, AgentHandle] = field(default_factory=dict)
    logger: logging.Logger = field(init=False, repr=False)

    def __post_init__(self) -> None:
        """在 dataclass 初始化完成后，构建工厂实例和日志记录器。"""
        self.factory = AgentFactory(self.settings)
        self.logger = logging.getLogger("zgraph.agent")

    def create_handle(self, run_id: str) -> AgentHandle:
        """为指定的运行标识符创建并缓存一个智能体运行句柄。

        参数:
            run_id: 本次运行的唯一标识符（str）。

        返回:
            新创建的智能体运行句柄（AgentHandle）。
        """
        handle = AgentHandle(run_id=run_id, cancellation=CancellationToken.create())
        self.handles[run_id] = handle
        return handle

    def run(
        self,
        run_id: str,
        user_input: str,
        tools: list[RuntimeTool],
        *,
        system_prompt: str | None = None,
        conversation_path: Path | None = None,
    ) -> AgentResult:
        """执行智能体运行流程并返回输出结果。

        参数:
            run_id: 本次运行的唯一标识符（str）。
            user_input: 用户输入的原始文本（str）。
            tools: 智能体可调用的运行时工具列表（list[RuntimeTool]）。
            system_prompt: 可选的系统提示词（str | None）。
            conversation_path: 可选的对话记录保存路径（Path | None）。

        返回:
            智能体生成的 AgentResult（含 content / reasoning_content）。
        """
        handle = self.create_handle(run_id)
        handle.status = "running"
        try:
            started = time.perf_counter()
            self.logger.info("run_id=%s stage=agent.create:start tools=%s", run_id, [tool.name for tool in tools])
            agent = self.factory.create(tools, system_prompt=system_prompt)
            self.logger.info(
                "run_id=%s stage=agent.create:end elapsed_ms=%.2f",
                run_id,
                (time.perf_counter() - started) * 1000,
            )
            started = time.perf_counter()
            self.logger.info("run_id=%s stage=agent.invoke:start", run_id)
            output = self.runner.run(
                agent,
                user_input,
                handle.cancellation,
                conversation_path=conversation_path,
            )
            self.logger.info(
                "run_id=%s stage=agent.invoke:end elapsed_ms=%.2f chars=%s reasoning_chars=%s conversation=%s",
                run_id,
                (time.perf_counter() - started) * 1000,
                len(output.content),
                len(output.reasoning_content),
                str(conversation_path) if conversation_path else "",
            )
            handle.status = "completed"
            return output
        except Exception as exc:
            self.logger.warning("run_id=%s stage=agent:error error=%s", run_id, exc)
            handle.status = "failed"
            raise

    def stop(self, run_id: str) -> bool:
        """停止指定运行标识符对应的智能体运行。

        参数:
            run_id: 要停止的运行标识符（str）。

        返回:
            如果找到并停止对应运行则返回 True，否则返回 False（bool）。
        """
        handle = self.handles.get(run_id)
        if handle is None:
            return False
        self.stopper.stop(handle)
        return True
