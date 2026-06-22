from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, Field

from zgraph.config import Settings
from zgraph.core.provider import build_chat_model
from zgraph.workflow.base import WorkflowResult
from zgraph.workflow.service.fix import FixWorkflow
from zgraph.workflow.service.structured import coerce_model_output
from zgraph.workflow.spec import WorkflowInputSpec, WorkflowSpec


class WorkflowSlotsOutput(BaseModel):
    """工作流槽位输出。继承自 BaseModel。"""
    slots: dict[str, Any] = Field(default_factory=dict)


@dataclass(slots=True)
class SlotResolutionResult:
    """槽位resolution结果。"""
    slots: dict[str, Any] = field(default_factory=dict)
    missing: list[str] = field(default_factory=list)
    auto_fixed: list[str] = field(default_factory=list)
    fixes: list[dict[str, Any]] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    source: str = "local"

    @property
    def ok(self) -> bool:
        """ok。
        
            返回:
                返回类型为 bool 的结果
            """
        return not self.missing and not self.errors


class WorkflowSlotResolver:
    """工作流槽位解析器。"""
    def __init__(self, settings: Settings, fix_workflow: FixWorkflow | None = None) -> None:
        """初始化实例属性。
        
            参数:
                settings: 设置（Settings）
                fix_workflow: fix工作流，可选，默认为 None（FixWorkflow | None）
            """
        self.settings = settings
        self.fix_workflow = fix_workflow or FixWorkflow(settings)

    def resolve(self, spec: WorkflowSpec, *, user_input: str, state: dict[str, Any]) -> SlotResolutionResult:
        """解析。
        
            参数:
                spec: 规范（WorkflowSpec）
        
            返回:
                返回类型为 SlotResolutionResult 的结果
            """
        slots = _defaults(spec)
        slots.update(_state_slots(state, spec))
        slots.update(_local_slots(user_input, spec))

        missing = _missing_required(spec, slots)
        source = "local"
        extraction_errors: list[str] = []
        if missing and not self.settings.offline and self.settings.api_key:
            try:
                extracted = self._llm_slots(spec, user_input=user_input, existing_slots=slots)
                slots.update(_known_slots(extracted, spec))
                source = "llm"
            except Exception as exc:
                extraction_errors.append(str(exc))
                source = "llm_error"

        missing = _missing_required(spec, slots)
        auto_fixed: list[str] = []
        fixes: list[dict[str, Any]] = []
        auto_fixable = [name for name in missing if spec.inputs.get(name) and spec.inputs[name].auto_fix]
        if auto_fixable:
            try:
                fixed, fixes = self._run_auto_fixes(
                    spec,
                    user_input=user_input,
                    existing_slots=slots,
                    missing=auto_fixable,
                )
            except Exception as exc:
                return SlotResolutionResult(
                    slots=slots,
                    missing=missing,
                    fixes=fixes,
                    errors=[str(exc)],
                    source="fix",
                )
            slots.update(fixed)
            auto_fixed = list(fixed.keys())
            if auto_fixed:
                source = "fix" if source == "local" else f"{source}+fix"

        missing = _missing_required(spec, slots)
        errors = extraction_errors if missing and extraction_errors else []
        return SlotResolutionResult(
            slots=slots,
            missing=missing,
            auto_fixed=auto_fixed,
            fixes=fixes,
            errors=errors,
            source=source,
        )

    def _llm_slots(self, spec: WorkflowSpec, *, user_input: str, existing_slots: dict[str, Any]) -> dict[str, Any]:
        """内部方法：llm槽位。
        
            参数:
                spec: 规范（WorkflowSpec）
        
            返回:
                返回类型为 dict[str, Any] 的结果
            """
        model = build_chat_model(self.settings)
        input_payload = {
            name: {
                "description": item.description,
                "required": item.required,
                "aliases": item.aliases,
                "default": item.default,
                "auto_fix": item.auto_fix,
                "schema": item.input_schema,
            }
            for name, item in spec.inputs.items()
        }
        messages = [
            {
                "role": "system",
                "content": (
                    "You extract workflow input slots from the user request. "
                    "Return JSON only: {\"slots\": {...}}. Do not invent values. "
                    "Only include values explicitly present in the user request."
                ),
            },
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "workflow": spec.name,
                        "inputs": input_payload,
                        "existing_slots": existing_slots,
                        "user_input": user_input,
                    },
                    ensure_ascii=False,
                ),
            },
        ]
        if self.settings.structured_output:
            output = model.with_structured_output(WorkflowSlotsOutput).invoke(messages)
        else:
            output = model.invoke(messages)
        return coerce_model_output(output, WorkflowSlotsOutput).slots

    def _run_auto_fixes(
        self,
        spec: WorkflowSpec,
        *,
        user_input: str,
        existing_slots: dict[str, Any],
        missing: list[str],
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        """内部方法：运行autofixes。
        
            参数:
                spec: 规范（WorkflowSpec）
        
            返回:
                返回类型为 tuple[dict[str, Any], list[dict[str, Any]]] 的结果
            """

        slots = dict(existing_slots)
        fixed: dict[str, Any] = {}
        fixes: list[dict[str, Any]] = []
        for name in missing:
            item = spec.inputs[name]
            result = self.fix_workflow.run(
                {
                    "workflow": spec.name,
                    "target_input": _fix_input_payload(name, item),
                    "existing_slots": slots,
                    "user_input": user_input,
                }
            )
            fixes.append(_fix_record(name, result))
            if result.status != "completed":
                raise ValueError("; ".join(result.errors) or f"fix failed for {name}")
            values = _values_from_fix_result(result, expected_name=name)
            known = _known_slots(values, spec)
            if name in known:
                slots[name] = known[name]
                fixed[name] = known[name]
        return fixed, fixes


def _fix_input_payload(name: str, item: WorkflowInputSpec) -> dict[str, Any]:
    """内部方法：fix输入载荷。
    
        参数:
            name: 名称（str）
            item: 项（WorkflowInputSpec）
    
        返回:
            返回类型为 dict[str, Any] 的结果
        """
    return {
        "name": name,
        "description": item.description,
        "required": item.required,
        "aliases": item.aliases,
        "default": item.default,
        "auto_fix": item.auto_fix,
        "auto_fix_hint": item.auto_fix_hint,
        "schema": item.input_schema,
    }


def _values_from_fix_result(result: WorkflowResult, *, expected_name: str) -> dict[str, Any]:
    """内部方法：valuesfromfix结果。
    
        参数:
            result: 结果（WorkflowResult）
    
        返回:
            返回类型为 dict[str, Any] 的结果
        """
    values: dict[str, Any] = {}
    data = result.data.get("data") if isinstance(result.data, dict) else []
    if not isinstance(data, list):
        raise ValueError(f"fix result for {expected_name} did not return data")
    for item in data:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        if name == expected_name:
            values[name] = item.get("value")
    if expected_name not in values or values[expected_name] in (None, "", [], {}):
        raise ValueError(f"fix result did not include a value for {expected_name}")
    return values


def _fix_record(name: str, result: WorkflowResult) -> dict[str, Any]:
    """内部方法：fix记录。
    
        参数:
            name: 名称（str）
            result: 结果（WorkflowResult）
    
        返回:
            返回类型为 dict[str, Any] 的结果
        """
    record: dict[str, Any] = {
        "workflow": result.name,
        "target": name,
        "status": result.status,
        "source": result.data.get("source") if isinstance(result.data, dict) else None,
    }
    if result.errors:
        record["errors"] = result.errors
    data = result.data.get("data") if isinstance(result.data, dict) else []
    if isinstance(data, list):
        for item in data:
            if isinstance(item, dict) and item.get("name") == name:
                record["fixed"] = item.get("value") not in (None, "", [], {})
                break
    return record


def _defaults(spec: WorkflowSpec) -> dict[str, Any]:
    """内部方法：defaults。
    
        参数:
            spec: 规范（WorkflowSpec）
    
        返回:
            返回类型为 dict[str, Any] 的结果
        """
    slots: dict[str, Any] = {}
    for name, item in spec.inputs.items():
        if item.default is not None:
            slots[name] = item.default
    return slots


def _state_slots(state: dict[str, Any], spec: WorkflowSpec) -> dict[str, Any]:
    """内部方法：状态槽位。
    
        参数:
            state: 状态（dict[str, Any]）
            spec: 规范（WorkflowSpec）
    
        返回:
            返回类型为 dict[str, Any] 的结果
        """
    hint = state.get("hint") if isinstance(state.get("hint"), dict) else {}
    raw = hint.get("slots") if isinstance(hint, dict) else {}
    if not isinstance(raw, dict):
        return {}
    return _known_slots(raw, spec)


def _known_slots(values: dict[str, Any], spec: WorkflowSpec) -> dict[str, Any]:
    """内部方法：known槽位。
    
        参数:
            values: values（dict[str, Any]）
            spec: 规范（WorkflowSpec）
    
        返回:
            返回类型为 dict[str, Any] 的结果
        """
    known: dict[str, Any] = {}
    for name in spec.inputs:
        value = values.get(name)
        if value not in (None, ""):
            known[name] = _coerce_slot(value, spec.inputs[name])
    return known


def _local_slots(user_input: str, spec: WorkflowSpec) -> dict[str, Any]:
    """内部方法：local槽位。
    
        参数:
            user_input: 用户输入（str）
            spec: 规范（WorkflowSpec）
    
        返回:
            返回类型为 dict[str, Any] 的结果
        """
    slots: dict[str, Any] = {}
    for name, item in spec.inputs.items():
        value = _extract_local_value(user_input, [name, *item.aliases])
        if value not in (None, ""):
            slots[name] = _coerce_slot(value, item)
    return slots


def _extract_local_value(text: str, labels: list[str]) -> str | None:
    """内部方法：抽取local值。
    
        参数:
            text: 文本（str）
            labels: labels（list[str]）
    
        返回:
            返回类型为 str | None 的结果
        """
    for label in labels:
        if not label:
            continue
        escaped = re.escape(label)
        patterns = (
            rf"{escaped}\s*[=:：]\s*[\"“']?(.+?)[\"”']?(?=\s*(?:[,，;；。]|\n|$))",
            rf"{escaped}\s*[\"“'](.+?)[\"”']",
        )
        for pattern in patterns:
            match = re.search(pattern, text)
            if match:
                return match.group(1).strip()
    return None


def _coerce_slot(value: Any, input_spec: WorkflowInputSpec) -> Any:
    """内部方法：coerce槽位。
    
        参数:
            value: 值（Any）
            input_spec: 输入规范（WorkflowInputSpec）
    
        返回:
            返回类型为 Any 的结果
        """
    schema_type = str((input_spec.input_schema or {}).get("type") or "").lower()
    if schema_type in {"array", "object"} and isinstance(value, str):
        try:
            parsed = json.loads(value)
            if schema_type == "array" and isinstance(parsed, list):
                return parsed
            if schema_type == "object" and isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            return value
    if isinstance(input_spec.default, bool):
        if isinstance(value, str):
            return value.strip().lower() in {"1", "true", "yes", "y", "on", "是"}
        return bool(value)
    if isinstance(input_spec.default, int) and not isinstance(input_spec.default, bool):
        try:
            return int(str(value).strip())
        except ValueError:
            return value
    return value


def _missing_required(spec: WorkflowSpec, slots: dict[str, Any]) -> list[str]:
    """内部方法：missing必需的。
    
        参数:
            spec: 规范（WorkflowSpec）
            slots: 槽位（dict[str, Any]）
    
        返回:
            返回类型为 list[str] 的结果
        """
    missing: list[str] = []
    for name, item in spec.inputs.items():
        if item.required and slots.get(name) in (None, "", [], {}):
            missing.append(name)
    return missing
