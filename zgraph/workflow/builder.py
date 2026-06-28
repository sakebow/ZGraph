from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml

from zgraph.workflow.base import WorkflowResult
from zgraph.workflow.spec import WorkflowSpec, parse_workflow_text


class DraftWorkflow:

    """draft工作流。"""
    def __init__(self, definition: dict[str, Any]) -> None:
        """初始化实例属性。
        
            参数:
                definition: definition（dict[str, Any]）
            """

        self.definition = definition
        self.name = str(definition.get("name", "draft_workflow"))

    def run(self, state: dict[str, Any]) -> WorkflowResult:
        """执行核心逻辑并返回结果。
        
            参数:
                state: 状态（dict[str, Any]）
        
            返回:
                返回类型为 WorkflowResult 的结果
            """

        steps = self.definition.get("steps") or []
        outputs: list[dict[str, Any]] = []
        for index, step in enumerate(steps):
            outputs.append(
                {
                    "index": index,
                    "name": step.get("name", f"step_{index}"),
                    "type": step.get("type", "noop"),
                    "status": "planned",
                }
            )
        return WorkflowResult(self.name, "completed", {"steps": outputs, "input": state})


class WorkflowBuilder:

    """工作流构建器。"""
    def from_file(self, path: Path) -> DraftWorkflow:
        """from文件。
        
            参数:
                path: 路径（Path）
        
            返回:
                返回类型为 DraftWorkflow 的结果
            """

        text = path.read_text(encoding="utf-8")
        if path.suffix.lower() in {".yaml", ".yml"}:
            definition = yaml.safe_load(text) or {}
        else:
            definition = json.loads(text)
        return DraftWorkflow(definition)

    def spec_from_file(self, path: Path) -> WorkflowSpec:
        """规范from文件。
        
            参数:
                path: 路径（Path）
        
            返回:
                返回类型为 WorkflowSpec 的结果
            """

        return parse_workflow_text(path.read_text(encoding="utf-8"), source=str(path))

    def spec_from_text(self, text: str, *, source: str = "workflow") -> WorkflowSpec:
        """规范from文本。
        
            参数:
                text: 文本（str）
        
            返回:
                返回类型为 WorkflowSpec 的结果
            """

        return parse_workflow_text(text, source=source)
