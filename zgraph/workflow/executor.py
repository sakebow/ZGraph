from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from typing import Any

from zgraph.core.register import Registry
from zgraph.core.tool.base import RuntimeTool, ToolResult
from zgraph.workflow.spec import WorkflowSpec, json_safe, validate_workflow_spec


_TEMPLATE_RE = re.compile(
    r"\{\{\s*([A-Za-z_][A-Za-z0-9_.-]*(?:\s*\|\s*[A-Za-z_][A-Za-z0-9_]*)*)\s*\}\}"
)
_MISSING = object()


@dataclass(slots=True)
class WorkflowStepRecord:
    """工作流步骤记录。"""
    id: str
    name: str
    type: str
    tool: str
    status: str
    attempts: int = 0
    args: dict[str, Any] = field(default_factory=dict)
    outputs: dict[str, Any] = field(default_factory=dict)
    content: str = ""
    data: dict[str, Any] = field(default_factory=dict)
    error: str = ""
    elapsed_ms: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        """to字典。
        
            返回:
                返回类型为 dict[str, Any] 的结果
            """
        return json_safe(
            {
                "id": self.id,
                "name": self.name,
                "type": self.type,
                "tool": self.tool,
                "status": self.status,
                "attempts": self.attempts,
                "args": self.args,
                "outputs": self.outputs,
                "content": self.content,
                "data": self.data,
                "error": self.error,
                "elapsed_ms": self.elapsed_ms,
            }
        )


@dataclass(slots=True)
class WorkflowExecutionResult:
    """工作流execution结果。"""
    name: str
    status: str
    steps: list[WorkflowStepRecord] = field(default_factory=list)
    variables: dict[str, Any] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)
    elapsed_ms: float = 0.0

    @property
    def ok(self) -> bool:
        """ok。
        
            返回:
                返回类型为 bool 的结果
            """
        return self.status == "completed"

    def to_dict(self) -> dict[str, Any]:
        """to字典。
        
            返回:
                返回类型为 dict[str, Any] 的结果
            """
        return json_safe(
            {
                "name": self.name,
                "status": self.status,
                "steps": [step.to_dict() for step in self.steps],
                "variables": self.variables,
                "errors": self.errors,
                "elapsed_ms": self.elapsed_ms,
            }
        )

    def to_text(self) -> str:
        """to文本。
        
            返回:
                返回类型为 str 的结果
            """
        payload = self.to_dict()
        return json.dumps(payload, ensure_ascii=False, indent=2)


class WorkflowExecutor:
    """工作流执行器。"""
    def __init__(self, tool_registry: Registry[RuntimeTool]) -> None:
        """初始化实例属性。
        
            参数:
                tool_registry: 工具注册表（Registry[RuntimeTool]）
            """
        self.tool_registry = tool_registry

    def run(
        self,
        spec: WorkflowSpec,
        *,
        initial_variables: dict[str, Any] | None = None,
        state: dict[str, Any] | None = None,
    ) -> WorkflowExecutionResult:
        """执行核心逻辑并返回结果。
        
            参数:
                spec: 规范（WorkflowSpec）
        
            返回:
                返回类型为 WorkflowExecutionResult 的结果
            """

        started = time.perf_counter()
        validation = validate_workflow_spec(spec, tool_registry=self.tool_registry)
        if not validation.valid:
            return WorkflowExecutionResult(
                name=spec.name,
                status="failed",
                errors=validation.errors,
                elapsed_ms=(time.perf_counter() - started) * 1000,
            )

        variables = dict(initial_variables or {})
        step_context: dict[str, dict[str, Any]] = {}
        completed: set[str] = set()
        records: list[WorkflowStepRecord] = []

        for step in spec.steps:
            missing_needs = [need for need in step.needs if need not in completed]
            if missing_needs:
                record = WorkflowStepRecord(
                    id=step.id,
                    name=step.name or step.id,
                    type=step.type,
                    tool=step.tool,
                    status="failed",
                    error=f"Unmet dependencies: {', '.join(missing_needs)}",
                )
                records.append(record)
                return _failed(spec.name, records, variables, record.error, started)

            rendered_args = _render_value(step.args, variables=variables, steps=step_context, state=state or {})
            record = WorkflowStepRecord(
                id=step.id,
                name=step.name or step.id,
                type=step.type,
                tool=step.tool,
                status="running",
                args=rendered_args if isinstance(rendered_args, dict) else {},
            )
            records.append(record)
            step_started = time.perf_counter()

            if step.type == "noop":
                tool_result = ToolResult(True, "", {})
                record.attempts = 1
            else:
                tool = self.tool_registry.get(step.tool)
                if tool is None:
                    record.status = "failed"
                    record.error = f"Unknown tool: {step.tool}"
                    record.elapsed_ms = (time.perf_counter() - step_started) * 1000
                    return _failed(spec.name, records, variables, record.error, started)
                args_error = _coerce_tool_args(tool, record)
                if args_error:
                    record.status = "failed"
                    record.error = args_error
                    record.elapsed_ms = (time.perf_counter() - step_started) * 1000
                    return _failed(spec.name, records, variables, record.error, started)
                tool_result = _run_tool_with_retries(tool, record.args, retries=step.retries, record=record)

            record.elapsed_ms = (time.perf_counter() - step_started) * 1000
            record.content = tool_result.content
            record.data = tool_result.data
            if not tool_result.ok:
                record.status = "failed"
                record.error = tool_result.content or f"Tool {step.tool!r} returned failure"
                return _failed(spec.name, records, variables, record.error, started)

            output_error = _capture_outputs(step.outputs, tool_result, variables, record)
            if output_error:
                record.status = "failed"
                record.error = output_error
                return _failed(spec.name, records, variables, output_error, started)

            step_context[step.id] = {
                "id": step.id,
                "name": step.name or step.id,
                "ok": tool_result.ok,
                "status": "completed",
                "content": tool_result.content,
                "data": tool_result.data,
                "outputs": record.outputs,
            }
            assertion_error = _run_assertions(step.assertions, variables=variables, steps=step_context, state=state or {})
            if assertion_error:
                record.status = "failed"
                record.error = assertion_error
                return _failed(spec.name, records, variables, assertion_error, started)

            record.status = "completed"
            completed.add(step.id)

        return WorkflowExecutionResult(
            name=spec.name,
            status="completed",
            steps=records,
            variables=variables,
            elapsed_ms=(time.perf_counter() - started) * 1000,
        )


def _run_tool_with_retries(
    tool: RuntimeTool,
    args: dict[str, Any],
    *,
    retries: int,
    record: WorkflowStepRecord,
) -> ToolResult:
    """内部方法：运行工具withretries。
    
        参数:
            tool: 工具（RuntimeTool）
            args: 位置参数
    
        返回:
            返回类型为 ToolResult 的结果
        """

    last = ToolResult(False, "tool did not run", {})
    for attempt in range(retries + 1):
        record.attempts = attempt + 1
        try:
            last = tool.run(**args)
        except Exception as exc:
            last = ToolResult(False, f"tool {tool.name!r} raised: {exc}", {})
        if last.ok:
            return last
    return last


def _coerce_tool_args(tool: RuntimeTool, record: WorkflowStepRecord) -> str:
    """内部方法：coerce工具参数。
    
        参数:
            tool: 工具（RuntimeTool）
            record: 记录（WorkflowStepRecord）
    
        返回:
            返回类型为 str 的结果
        """
    if tool.args_schema is None:
        return ""
    try:
        record.args = tool.args_schema.model_validate(record.args).model_dump()
    except Exception as exc:
        return f"Tool {tool.name!r} argument validation failed: {exc}"
    return ""


def _capture_outputs(
    outputs: dict[str, str],
    result: ToolResult,
    variables: dict[str, Any],
    record: WorkflowStepRecord,
) -> str:
    """内部方法：captureoutputs。
    
        参数:
            outputs: outputs（dict[str, str]）
            result: 结果（ToolResult）
            variables: 变量（dict[str, Any]）
            record: 记录（WorkflowStepRecord）
    
        返回:
            返回类型为 str 的结果
        """

    for variable, expression in outputs.items():
        value = _extract_output(expression, result)
        if value is _MISSING:
            return f"Output {variable!r} could not be extracted from {expression!r}"
        variables[variable] = value
        record.outputs[variable] = value
    return ""


def _extract_output(expression: str, result: ToolResult) -> Any:
    """内部方法：抽取输出。
    
        参数:
            expression: 表达式（str）
            result: 结果（ToolResult）
    
        返回:
            返回类型为 Any 的结果
        """
    expr = str(expression).strip()
    if expr.startswith("$."):
        expr = "json." + expr[2:]
    if expr == "ok":
        return result.ok
    if expr == "content":
        return result.content
    if expr == "data":
        return result.data
    if expr.startswith("data."):
        return _dig(result.data, expr.split(".")[1:])
    if expr == "json":
        return _parse_json(result.content)
    if expr.startswith("json."):
        parsed = _parse_json(result.content)
        if parsed is _MISSING:
            return _MISSING
        return _dig(parsed, expr.split(".")[1:])
    return _MISSING


def _parse_json(text: str) -> Any:
    """内部方法：解析JSON。
    
        参数:
            text: 文本（str）
    
        返回:
            返回类型为 Any 的结果
        """
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return _MISSING


def _dig(value: Any, parts: list[str]) -> Any:
    """内部方法：dig。
    
        参数:
            value: 值（Any）
            parts: parts（list[str]）
    
        返回:
            返回类型为 Any 的结果
        """
    current = value
    for part in parts:
        if isinstance(current, dict):
            if part not in current:
                return _MISSING
            current = current[part]
        elif isinstance(current, list) and part.isdigit():
            index = int(part)
            if index >= len(current):
                return _MISSING
            current = current[index]
        else:
            return _MISSING
    return current


def _render_value(value: Any, *, variables: dict[str, Any], steps: dict[str, Any], state: dict[str, Any]) -> Any:
    """内部方法：渲染值。
    
        参数:
            value: 值（Any）
    
        返回:
            返回类型为 Any 的结果
        """
    if isinstance(value, str):
        return _render_string(value, variables=variables, steps=steps, state=state)
    if isinstance(value, dict):
        return {key: _render_value(item, variables=variables, steps=steps, state=state) for key, item in value.items()}
    if isinstance(value, list):
        return [_render_value(item, variables=variables, steps=steps, state=state) for item in value]
    return value


def _render_string(value: str, *, variables: dict[str, Any], steps: dict[str, Any], state: dict[str, Any]) -> Any:
    """内部方法：渲染字符串。
    
        参数:
            value: 值（str）
    
        返回:
            返回类型为 Any 的结果
        """
    full_match = _TEMPLATE_RE.fullmatch(value.strip())
    if full_match:
        looked_up = _lookup(full_match.group(1), variables=variables, steps=steps, state=state)
        return "" if looked_up is _MISSING else looked_up

    def replace(match: re.Match[str]) -> str:
        """replace。
        
            参数:
                match: 匹配（re.Match[str]）
        
            返回:
                返回类型为 str 的结果
            """
        looked_up = _lookup(match.group(1), variables=variables, steps=steps, state=state)
        return "" if looked_up is _MISSING else str(looked_up)

    return _TEMPLATE_RE.sub(replace, value)


def _lookup(expression: str, *, variables: dict[str, Any], steps: dict[str, Any], state: dict[str, Any]) -> Any:
    """内部方法：查询。
    
        参数:
            expression: 表达式（str）
    
        返回:
            返回类型为 Any 的结果
        """
    expression, filters = _split_filters(expression)
    if expression in variables:
        return _apply_filters(variables[expression], filters)
    if expression.startswith("steps."):
        return _apply_filters(_dig({"steps": steps}, expression.split(".")), filters)
    if expression.startswith("state."):
        return _apply_filters(_dig({"state": state}, expression.split(".")), filters)
    return _apply_filters(_dig(variables, expression.split(".")), filters)


def _split_filters(expression: str) -> tuple[str, list[str]]:
    """内部方法：拆分filters。
    
        参数:
            expression: 表达式（str）
    
        返回:
            返回类型为 tuple[str, list[str]] 的结果
        """
    parts = [part.strip() for part in expression.split("|")]
    return parts[0], [part for part in parts[1:] if part]


def _apply_filters(value: Any, filters: list[str]) -> Any:
    """内部方法：应用filters。
    
        参数:
            value: 值（Any）
            filters: filters（list[str]）
    
        返回:
            返回类型为 Any 的结果
        """
    if value is _MISSING:
        return value
    rendered = value
    for item in filters:
        if item == "json":
            rendered = json.dumps(rendered, ensure_ascii=False)
        elif item == "str":
            rendered = str(rendered)
        else:
            return _MISSING
    return rendered


def _run_assertions(
    assertions: list[str],
    *,
    variables: dict[str, Any],
    steps: dict[str, Any],
    state: dict[str, Any],
) -> str:
    """内部方法：运行assertions。
    
        参数:
            assertions: assertions（list[str]）
    
        返回:
            返回类型为 str 的结果
        """

    for assertion in assertions:
        error = _evaluate_assertion(str(assertion), variables=variables, steps=steps, state=state)
        if error:
            return error
    return ""


def _evaluate_assertion(
    assertion: str,
    *,
    variables: dict[str, Any],
    steps: dict[str, Any],
    state: dict[str, Any],
) -> str:
    """内部方法：evaluateassertion。
    
        参数:
            assertion: assertion（str）
    
        返回:
            返回类型为 str 的结果
        """

    text = assertion.strip()
    exists_match = re.fullmatch(r"(.+?)\s+exists", text)
    if exists_match:
        value = _lookup(exists_match.group(1).strip(), variables=variables, steps=steps, state=state)
        if value in (_MISSING, None, "", [], {}):
            return f"Assertion failed: {text}"
        return ""

    compare_match = re.fullmatch(r"(.+?)\s*(==|!=)\s*(.+)", text)
    if compare_match:
        left = _lookup(compare_match.group(1).strip(), variables=variables, steps=steps, state=state)
        right = _literal(compare_match.group(3).strip())
        if compare_match.group(2) == "==" and left != right:
            return f"Assertion failed: {text}"
        if compare_match.group(2) == "!=" and left == right:
            return f"Assertion failed: {text}"
        return ""

    value = _lookup(text, variables=variables, steps=steps, state=state)
    if not value:
        return f"Assertion failed: {text}"
    return ""


def _literal(value: str) -> Any:
    """内部方法：literal。
    
        参数:
            value: 值（str）
    
        返回:
            返回类型为 Any 的结果
        """
    if value in {"true", "True"}:
        return True
    if value in {"false", "False"}:
        return False
    if value in {"null", "None"}:
        return None
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    try:
        return int(value)
    except ValueError:
        return value


def _failed(
    name: str,
    records: list[WorkflowStepRecord],
    variables: dict[str, Any],
    error: str,
    started: float,
) -> WorkflowExecutionResult:
    """内部方法：failed。
    
        参数:
            name: 名称（str）
            records: records（list[WorkflowStepRecord]）
            variables: 变量（dict[str, Any]）
            error: 错误（str）
            started: started（float）
    
        返回:
            返回类型为 WorkflowExecutionResult 的结果
        """

    return WorkflowExecutionResult(
        name=name,
        status="failed",
        steps=records,
        variables=variables,
        errors=[error],
        elapsed_ms=(time.perf_counter() - started) * 1000,
    )
