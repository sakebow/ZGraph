from __future__ import annotations

from typing import Any, Sequence

from zgraph.config import Settings
from zgraph.core.provider import build_chat_model
from zgraph.core.tool.base import RuntimeTool


class AgentFactory:

    """智能体工厂，负责根据应用配置创建 LangChain 智能体实例。"""
    def __init__(self, settings: Settings) -> None:
        """初始化智能体工厂。
            参数:
                settings: 应用配置对象，用于构建底层语言模型（Settings）。
            """
        self.settings = settings

    def create(self, tools: Sequence[RuntimeTool], *, system_prompt: str | None = None) -> Any:
        """创建并返回一个配置好的 LangChain 智能体实例。
            参数:
                tools: 智能体可调用的运行时工具集合（Sequence[RuntimeTool]）。
                system_prompt: 可选的系统提示词，未提供时使用配置中的默认值（str | None）。
            返回:
                创建完成的 LangChain 智能体对象（Any）。
            """
        from langchain.agents import create_agent

        model = build_chat_model(self.settings)
        langchain_tools = [tool.to_langchain_tool() for tool in tools]
        return create_agent(
            model=model,
            tools=langchain_tools,
            system_prompt=system_prompt or self.settings.system_prompt,
        )
