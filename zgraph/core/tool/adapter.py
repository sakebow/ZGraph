from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel, Field

from zgraph.core.adapter.configured import ConfiguredAdapterEngine
from zgraph.core.tool.base import RuntimeTool, ToolResult


class AdapterCallArgs(BaseModel):
    """适配器调用工具的参数模型。
        参数:
            app: ZGRAPH_HOME/apps 下的应用目录名称。
            adapter: adapters.yaml 中配置的适配器名称。
            action: 适配器下定义的动作名称。
            params: 调用动作时传入的参数字典。
            timeout_seconds: 请求超时时间，单位为秒，范围 1-300。
    """

    app: str = Field(default="", description="App directory name under ZGRAPH_HOME/apps.")
    adapter: str = Field(description="Configured adapter name from adapters.yaml.")
    action: str = Field(description="Configured adapter action name.")
    params: dict[str, Any] = Field(default_factory=dict)
    timeout_seconds: int = Field(default=60, ge=1, le=300)


class AdapterCallTool(RuntimeTool):
    """调用 ZGRAPH_HOME/apps/*/adapters.yaml 中配置适配器动作的运行时工具。"""

    name = "adapter.call"
    description = "Call an app-local configured adapter action from ZGRAPH_HOME/apps/*/adapters.yaml."
    risk_level = "high"
    retrievable = False
    tags = ("adapter", "workflow", "http", "api", "configured")
    args_schema = AdapterCallArgs

    def run(
        self,
        adapter: str,
        action: str,
        app: str = "",
        params: dict[str, Any] | None = None,
        timeout_seconds: int = 60,
    ) -> ToolResult:
        """执行已配置适配器动作并返回工具结果。
            参数:
                adapter: 适配器名称。
                action: 动作名称。
                app: 应用目录名称。
                params: 调用动作时传入的参数字典。
                timeout_seconds: 请求超时时间，单位为秒。
            返回:
                工具执行结果对象。
        """

        result = ConfiguredAdapterEngine(zgraph_home=self.context.workspace.root).call(
            app=app,
            adapter=adapter,
            action=action,
            params=params or {},
            timeout_seconds=timeout_seconds,
        )
        payload = result.to_payload()
        content = json.dumps(payload, ensure_ascii=False)
        return ToolResult(result.ok, content, result.data if result.ok else {})


DEFAULT_ADAPTER_TOOL_TYPES: tuple[type[RuntimeTool], ...] = (AdapterCallTool,)
