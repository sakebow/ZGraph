from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Literal, Type

from pydantic import BaseModel

from zgraph.workspace import RunWorkspace

RiskLevel = Literal["low", "medium", "high"]


@dataclass(slots=True)
class ToolResult:

    """工具结果。"""
    ok: bool
    content: str
    data: dict[str, Any] = field(default_factory=dict)

    def to_text(self) -> str:
        """转文本"""

        if self.data:
            return json.dumps(
                {"ok": self.ok, "content": self.content, "data": self.data},
                ensure_ascii=False,
            )
        return self.content


@dataclass(slots=True)
class ToolContext:

    """工具上下文。"""
    workspace: RunWorkspace
    allow_bash: bool = False
    todo_list: list[dict[str, Any]] = field(default_factory=list)
    interrupts: dict[str, dict[str, Any]] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)


class RuntimeTool:

    """运行时工具。"""
    name: str = ""
    description: str = ""
    risk_level: RiskLevel = "low"
    tags: tuple[str, ...] = ()
    required: bool = False
    retrievable: bool = True
    args_schema: Type[BaseModel] | None = None

    def __init__(self, context: ToolContext) -> None:
        """初始化实例属性"""

        self.context = context

    def run(self, **kwargs: Any) -> ToolResult:
        """执行核心逻辑并返回结果"""

        raise NotImplementedError

    def invoke(self, **kwargs: Any) -> str:
        """调用"""

        return self.run(**kwargs).to_text()

    def to_langchain_tool(self) -> Any:
        """转langchain"""

        from langchain_core.tools import StructuredTool

        def _run(**kwargs: Any) -> str:
            """内部方法：运行"""
            return self.invoke(**kwargs)

        safe_name = re.sub(r"[^A-Za-z0-9_-]", "_", self.name)
        _run.__name__ = safe_name
        return StructuredTool.from_function(
            func=_run,
            name=safe_name,
            description=self.description,
            args_schema=self.args_schema,
        )
