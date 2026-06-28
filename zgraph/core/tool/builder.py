from __future__ import annotations

from zgraph.core.register import Registry
from zgraph.core.tool.adapter import DEFAULT_ADAPTER_TOOL_TYPES
from zgraph.core.tool.base import RuntimeTool, ToolContext
from zgraph.core.tool.tools import DEFAULT_TOOL_TYPES


def build_default_tool_registry(context: ToolContext) -> Registry[RuntimeTool]:
    """构建默认值工具注册表。
        参数:
            context: 上下文（ToolContext）
        返回:
            返回类型为 Registry[RuntimeTool] 的结果
        """
    registry: Registry[RuntimeTool] = Registry("tools")
    for tool_type in (*DEFAULT_TOOL_TYPES, *DEFAULT_ADAPTER_TOOL_TYPES):
        tool = tool_type(context)
        registry.register(tool.name, tool)
    return registry
