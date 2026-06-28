from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field

from zgraph.core.register import Registry
from zgraph.core.tool.base import RuntimeTool


STEP_ID_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_-]*$")
VARIABLE_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
OUTPUT_EXPRESSION_RE = re.compile(r"^(ok|content|data(?:\.[A-Za-z0-9_]+)*|json(?:\.[A-Za-z0-9_]+)*|\$\.[A-Za-z0-9_]+(?:\.[A-Za-z0-9_]+)*)$")
ASSERT_EXISTS_RE = re.compile(r"^(.+?)\s+exists$")
ASSERT_COMPARE_RE = re.compile(r"^(.+?)\s*(==|!=)\s*(.+)$")
SHELL_DATE_RE = re.compile(r"(?i)(\bGet-Date\b|\bdate\s+\+)")


class WorkflowStepSpec(BaseModel):
    """工作流步骤规范。继承自 BaseModel。"""
    model_config = ConfigDict(populate_by_name=True, extra="allow")

    id: str
    name: str = ""
    type: Literal["tool", "noop"] = "tool"
    tool: str = ""
    needs: list[str] = Field(default_factory=list)
    args: dict[str, Any] = Field(default_factory=dict)
    outputs: dict[str, str] = Field(default_factory=dict)
    assertions: list[str] = Field(default_factory=list, alias="assert")
    retries: int = Field(default=0, ge=0, le=3)


class WorkflowInputSpec(BaseModel):
    """工作流输入规范。继承自 BaseModel。"""
    model_config = ConfigDict(populate_by_name=True, extra="allow")

    description: str = ""
    required: bool = False
    auto_fix: bool = False
    auto_fix_hint: str = ""
    input_schema: dict[str, Any] = Field(default_factory=dict, alias="schema")
    default: Any = None
    aliases: list[str] = Field(default_factory=list)


class WorkflowSpec(BaseModel):
    """工作流规范。继承自 BaseModel。"""
    model_config = ConfigDict(extra="allow")

    name: str = "temporary_workflow"
    version: str = "1"
    mode: Literal["sequential"] = "sequential"
    description: str = ""
    skill: str = ""
    inputs: dict[str, WorkflowInputSpec] = Field(default_factory=dict)
    steps: list[WorkflowStepSpec] = Field(default_factory=list)


@dataclass(slots=True)
class WorkflowValidationResult:
    """工作流校验结果。"""
    valid: bool
    errors: list[str] = field(default_factory=list)


def parse_workflow_text(text: str, *, source: str = "workflow") -> WorkflowSpec:
    """解析工作流文本"""
    payload = yaml.safe_load(text) or {}
    if not isinstance(payload, dict):
        raise ValueError(f"{source} must contain a YAML object")
    return WorkflowSpec.model_validate(payload)


def workflow_to_yaml(spec: WorkflowSpec) -> str:
    """工作流转YAML"""
    return yaml.safe_dump(spec.model_dump(by_alias=True, exclude_none=True), allow_unicode=True, sort_keys=False)


def validate_workflow_spec(
    spec: WorkflowSpec,
    *,
    tool_registry: Registry[RuntimeTool] | None = None,
) -> WorkflowValidationResult:
    """校验工作流规范"""

    errors: list[str] = []
    if not spec.steps:
        errors.append("workflow.steps must contain at least one step")
    for name in spec.inputs:
        if not VARIABLE_RE.match(name):
            errors.append(f"inputs contains invalid name {name!r}")

    seen: set[str] = set()
    for index, step in enumerate(spec.steps):
        prefix = f"steps[{index}]"
        if not STEP_ID_RE.match(step.id):
            errors.append(f"{prefix}.id must match {STEP_ID_RE.pattern}")
        if step.id in seen:
            errors.append(f"{prefix}.id duplicates earlier step {step.id!r}")
        seen.add(step.id)

        if step.type == "tool":
            if not step.tool:
                errors.append(f"{prefix}.tool is required for tool steps")
            elif tool_registry is not None and tool_registry.get(step.tool) is None:
                errors.append(f"{prefix}.tool references unknown tool {step.tool!r}")
        elif step.outputs:
            errors.append(f"{prefix}.outputs is only supported for tool steps")

        for need in step.needs:
            if need == step.id:
                errors.append(f"{prefix}.needs cannot reference itself")
            if need not in seen:
                errors.append(f"{prefix}.needs references missing or later step {need!r}")

        for variable in step.outputs:
            if not VARIABLE_RE.match(variable):
                errors.append(f"{prefix}.outputs contains invalid variable name {variable!r}")
        for variable, expression in step.outputs.items():
            if not OUTPUT_EXPRESSION_RE.match(str(expression).strip()):
                errors.append(
                    f"{prefix}.outputs.{variable} has unsupported expression {expression!r}; "
                    "use content, ok, data.*, json.*, or $.*"
                )

        if not isinstance(step.args, dict):
            errors.append(f"{prefix}.args must be an object")
        if step.type == "noop" and "assert" in step.args:
            errors.append(f"{prefix}.args.assert is ignored; put assertions at the step-level 'assert' key")
        if _contains_shell_date_command(step.args):
            errors.append(f"{prefix}.args contains shell date command; use the datetime tool instead")

        available_variables = _available_variables(spec.steps[: index + 1])
        for assertion in step.assertions:
            target = _assertion_target(str(assertion))
            if target and not _is_assertion_target_allowed(target, available_variables):
                errors.append(
                    f"{prefix}.assert contains unsupported target {target!r}; "
                    "assertions must reference declared output variables, state.*, or steps.*"
                )

        if tool_registry is not None and step.type == "tool" and step.tool:
            _validate_tool_args(step, index, tool_registry, errors)

    return WorkflowValidationResult(valid=not errors, errors=errors)


def _validate_tool_args(
    step: WorkflowStepSpec,
    index: int,
    tool_registry: Registry[RuntimeTool],
    errors: list[str],
) -> None:
    """校验工具参数"""

    tool = tool_registry.get(step.tool)
    if tool is None or tool.args_schema is None:
        return
    if _contains_template(step.args):
        return
    try:
        tool.args_schema.model_validate(step.args)
    except Exception as exc:
        errors.append(f"steps[{index}].args failed {step.tool!r} schema validation: {exc}")


def _contains_template(value: Any) -> bool:
    """存在模板"""
    if isinstance(value, str):
        return "{{" in value and "}}" in value
    if isinstance(value, dict):
        return any(_contains_template(item) for item in value.values())
    if isinstance(value, list):
        return any(_contains_template(item) for item in value)
    return False


def _contains_shell_date_command(value: Any) -> bool:
    """带有日期命令"""
    if isinstance(value, str):
        return bool(SHELL_DATE_RE.search(value))
    if isinstance(value, dict):
        return any(_contains_shell_date_command(item) for item in value.values())
    if isinstance(value, list):
        return any(_contains_shell_date_command(item) for item in value)
    return False


def _available_variables(steps: list[WorkflowStepSpec]) -> set[str]:
    """带有变量"""
    variables: set[str] = set()
    for step in steps:
        variables.update(step.outputs.keys())
    return variables


def _assertion_target(assertion: str) -> str:
    """错误提示"""
    text = assertion.strip()
    exists_match = ASSERT_EXISTS_RE.fullmatch(text)
    if exists_match:
        return exists_match.group(1).strip()
    compare_match = ASSERT_COMPARE_RE.fullmatch(text)
    if compare_match:
        return compare_match.group(1).strip()
    if text:
        return text
    return ""


def _is_assertion_target_allowed(target: str, available_variables: set[str]) -> bool:
    if target in available_variables:
        return True
    if target.startswith("state.") or target.startswith("steps."):
        return True
    return False


def json_safe(value: Any) -> Any:
    """JSON安全检查"""
    try:
        json.dumps(value, ensure_ascii=False)
        return value
    except TypeError:
        if isinstance(value, dict):
            return {str(key): json_safe(item) for key, item in value.items()}
        if isinstance(value, (list, tuple)):
            return [json_safe(item) for item in value]
        return str(value)
