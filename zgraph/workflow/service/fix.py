from __future__ import annotations

import json
import re
from typing import Any

from pydantic import BaseModel, Field

from zgraph.config import Settings
from zgraph.core.provider import build_chat_model_with_fallback
from zgraph.workflow.base import WorkflowResult
from zgraph.workflow.service.structured import coerce_model_output, extract_json_object


class FixedWorkflowInput(BaseModel):

    """fixed工作流输入。继承自 BaseModel。"""
    name: str = Field(description="The workflow input name being fixed.")
    value: Any = Field(description="The generated workflow input value.")


class FixWorkflowOutput(BaseModel):

    """fix工作流输出。继承自 BaseModel。"""
    data: list[FixedWorkflowInput] = Field(default_factory=list)


FIX_SYSTEM_PROMPT = """You fix exactly one missing ZGraph workflow input.

Return only the structured schema:
{"data": [{"name": "...", "value": "..."}]}

Rules:
- Solve only the target input.
- The returned name must exactly equal the target input name.
- Do not modify, rewrite, or propose workflow steps.
- Do not return fields other than the target input name.
- Use the user request, existing fixed inputs, target description, and fix hint.
- If target_input.schema is present, the value must match that schema.
- If the schema says integer or enum [0, 1], return JSON numbers 0/1, never booleans true/false.
- Generate concise executable request content, not explanations.
- If the fix hint provides a default placeholder, use it when the user did not specify a value.
"""


class FixWorkflow:
    """fix工作流。"""
    name = "fix"

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def run(self, state: dict[str, Any]) -> WorkflowResult:
        target = _target_name(state)
        if not target:
            return WorkflowResult(self.name, "failed", {}, ["fix target input name is required"])

        if not self.settings.offline and self.settings.api_key:
            try:
                output = self._run_llm(state, target=target)
                payload = _payload_for_target(output, target)
                payload["source"] = "llm"
                payload["target"] = target
                return WorkflowResult(self.name, "completed", payload)
            except Exception:
                pass

        output = self._fallback(state, target=target)
        payload = _payload_for_target(output, target)
        payload["source"] = "fallback"
        payload["target"] = target
        return WorkflowResult(self.name, "completed", payload)

    def _run_llm(self, state: dict[str, Any], *, target: str) -> FixWorkflowOutput:
        model = build_chat_model_with_fallback(self.settings)
        messages = [
            {"role": "system", "content": FIX_SYSTEM_PROMPT},
            {"role": "user", "content": json.dumps(_llm_payload(state, target), ensure_ascii=False)},
        ]
        if self.settings.structured_output:
            try:
                structured_model = model.with_structured_output(FixWorkflowOutput)
                output = coerce_model_output(structured_model.invoke(messages), FixWorkflowOutput)
                _ensure_target(output, target)
                return output
            except Exception:
                pass

        json_messages = [
            {
                "role": "system",
                "content": FIX_SYSTEM_PROMPT + "\nReturn a single strict JSON object matching the schema. No markdown.",
            },
            {"role": "user", "content": json.dumps(_llm_payload(state, target), ensure_ascii=False)},
        ]
        output = _coerce_fix_output(model.invoke(json_messages), target=target)
        _ensure_target(output, target)
        return output

    def _fallback(self, state: dict[str, Any], *, target: str) -> FixWorkflowOutput:
        target_spec = state.get("target_input") if isinstance(state.get("target_input"), dict) else {}
        hint = str(target_spec.get("auto_fix_hint") or "")
        schema = target_spec.get("schema") if isinstance(target_spec.get("schema"), dict) else {}
        user_input = str(state.get("user_input") or "")
        topic = _topic_from_user_input(user_input)
        value: Any = None
        if str(schema.get("type") or "").lower() == "array":
            value = _fallback_array(target, topic)
        if value is None:
            value = _hint_placeholder(hint)
        if value is None:
            value = _fallback_value(target, topic)
        return FixWorkflowOutput(data=[FixedWorkflowInput(name=target, value=value)])


def _target_name(state: dict[str, Any]) -> str:
    target = state.get("target_input") if isinstance(state.get("target_input"), dict) else {}
    return str(target.get("name") or state.get("target") or "").strip()


def _llm_payload(state: dict[str, Any], target: str) -> dict[str, Any]:
    target_input = state.get("target_input") if isinstance(state.get("target_input"), dict) else {}
    return {
        "workflow": state.get("workflow"),
        "target_input": target_input,
        "target": target,
        "existing_slots": state.get("existing_slots") if isinstance(state.get("existing_slots"), dict) else {},
        "user_input": state.get("user_input", ""),
    }


def _coerce_fix_output(result: Any, *, target: str) -> FixWorkflowOutput:
    try:
        return coerce_model_output(result, FixWorkflowOutput)
    except Exception:
        content = getattr(result, "content", result)
        if isinstance(content, list):
            content = " ".join(str(item.get("text", item)) if isinstance(item, dict) else str(item) for item in content)
        if not isinstance(content, str):
            raise
        payload = json.loads(extract_json_object(content))
        data = payload.get("data")
        if isinstance(data, list):
            items: list[FixedWorkflowInput] = []
            for item in data:
                if not isinstance(item, dict):
                    continue
                value = item.get("value", item.get("message", item.get("content")))
                if value in (None, "", [], {}):
                    continue
                items.append(FixedWorkflowInput(name=str(item.get("name") or target), value=value))
            if items:
                return FixWorkflowOutput(data=items)
        slots = payload.get("slots")
        if isinstance(slots, dict):
            return FixWorkflowOutput(
                data=[
                    FixedWorkflowInput(name=str(name), value=value)
                    for name, value in slots.items()
                ]
            )
        raise


def _payload_for_target(output: FixWorkflowOutput, target: str) -> dict[str, Any]:
    for item in output.data:
        if item.name == target and _is_useful_value(item.value):
            return {"data": [item.model_dump()]}
    usable = [item for item in output.data if _is_useful_value(item.value)]
    if len(usable) == 1:
        return {"data": [{"name": target, "value": usable[0].value}]}
    raise ValueError(f"fix output did not include a value for {target!r}")


def _ensure_target(output: FixWorkflowOutput, target: str) -> None:
    _payload_for_target(output, target)


def _is_useful_value(value: Any) -> bool:
    if value in (None, "", [], {}):
        return False
    if isinstance(value, str):
        normalized = value.strip().lower().strip(".!? ")
        if normalized in {"", "...", "todo", "tbd", "none", "null", "unknown", "placeholder"}:
            return False
        if value.strip() in {"...", "待填写", "占位符"}:
            return False
    return True


def _hint_placeholder(hint: str) -> str | None:
    match = re.search(r"\b[A-Z][A-Z0-9_]{2,}\b", hint)
    if match:
        return match.group(0)
    quoted_cn = re.search(r"“([^”]+)”", hint)
    if quoted_cn:
        return quoted_cn.group(1).strip()
    quoted = re.search(r"[\"']([^\"']+)[\"']", hint)
    if quoted:
        return quoted.group(1).strip()
    return None


def _fallback_array(target: str, topic: str) -> list[dict[str, Any]] | None:
    lowered = target.lower()
    if "option" not in lowered and "answer" not in lowered:
        return None
    return [
        {"optionCode": "A", "optionContent": f"{topic} basic correct answer", "isCorrect": 1, "sortOrder": 1},
        {"optionCode": "B", "optionContent": f"{topic} distractor B", "isCorrect": 0, "sortOrder": 2},
        {"optionCode": "C", "optionContent": f"{topic} distractor C", "isCorrect": 0, "sortOrder": 3},
        {"optionCode": "D", "optionContent": f"{topic} distractor D", "isCorrect": 0, "sortOrder": 4},
    ]


def _fallback_value(target: str, topic: str) -> str:
    lowered = target.lower()
    if target == "storeId" or lowered.endswith("storeid"):
        return "TEST_STORE_ID"
    if target == "storeName" or lowered.endswith("storename"):
        return "默认门店"
    if "bank" in lowered:
        return f"{topic}基础题库"
    if "question" in lowered:
        return f"以下哪项是{topic}中的基础要求？"
    if "option" in lowered or "answer" in lowered:
        return f"{topic}基础操作"
    if "paper" in lowered:
        return f"{topic}基础试卷"
    if "exam" in lowered or "plan" in lowered:
        return f"{topic}考试计划"
    return f"{topic} {target}"


def _topic_from_user_input(user_input: str) -> str:
    text = " ".join(user_input.strip().split())
    if not text:
        return "workflow"
    if re.search(r"[\u4e00-\u9fff]", text):
        cleaned = text
        for token in ("帮我", "请", "创建", "生成", "制定", "一个", "考试", "计划", "的"):
            cleaned = cleaned.replace(token, "")
        cleaned = " ".join(cleaned.split()).strip(" ，。,.")
        return cleaned[:20] or "通用"
    text = re.sub(r"(?i)\b(create|build|make|generate|help|me|please|plan|exam)\b", " ", text)
    text = " ".join(text.split()).strip(" ,.:;!?")
    return text[:40] or "workflow"
