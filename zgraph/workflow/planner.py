from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, Field

from zgraph.config import Settings
from zgraph.core.provider import build_chat_model
from zgraph.core.skills.loader import Skill
from zgraph.core.tool.base import RuntimeTool
from zgraph.workflow.service.structured import coerce_model_output


class WorkflowPlanOutput(BaseModel):
    """工作流plan输出。继承自 BaseModel。"""
    workflow_yaml: str = Field(description="A complete temporary workflow.yaml document.")
    notes: str = ""


class WorkflowReviewOutput(BaseModel):
    """工作流review输出。继承自 BaseModel。"""
    approved: bool
    issues: list[str] = Field(default_factory=list)
    corrected_workflow_yaml: str = ""


@dataclass(slots=True)
class TemporaryWorkflowPlanner:
    """temporary工作流规划器。"""
    settings: Settings

    def plan(
        self,
        *,
        user_input: str,
        state: dict[str, Any],
        skills: list[Skill],
        tools: list[RuntimeTool],
    ) -> WorkflowPlanOutput:
        """plan。
        
            返回:
                返回类型为 WorkflowPlanOutput 的结果
            """

        if self.settings.offline or not self.settings.api_key:
            return WorkflowPlanOutput(
                workflow_yaml=_offline_unavailable_workflow(),
                notes="LLM workflow planning is unavailable because provider execution is offline.",
            )

        model = build_chat_model(self.settings)
        messages = [
            {"role": "system", "content": _planner_system_prompt()},
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "user_input": user_input,
                        "state_hint": state.get("hint", {}),
                        "state_intent": state.get("intent", {}),
                        "skills": [_skill_payload(skill) for skill in skills],
                        "tools": [_tool_payload(tool) for tool in tools],
                    },
                    ensure_ascii=False,
                ),
            },
        ]
        if self.settings.structured_output:
            output = model.with_structured_output(WorkflowPlanOutput).invoke(messages)
        else:
            output = model.invoke(messages)
        plan = coerce_model_output(output, WorkflowPlanOutput)
        plan.workflow_yaml = _strip_fenced_block(plan.workflow_yaml)
        return plan


@dataclass(slots=True)
class TemporaryWorkflowReviewer:
    """temporary工作流审核器。"""
    settings: Settings

    def review(
        self,
        *,
        user_input: str,
        workflow_yaml: str,
        skills: list[Skill],
        tools: list[RuntimeTool],
    ) -> WorkflowReviewOutput:
        """review。
        
            返回:
                返回类型为 WorkflowReviewOutput 的结果
            """

        if self.settings.offline or not self.settings.api_key:
            return WorkflowReviewOutput(approved=True, issues=["LLM reviewer skipped in offline mode."])

        model = build_chat_model(self.settings)
        messages = [
            {"role": "system", "content": _reviewer_system_prompt()},
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "user_input": user_input,
                        "workflow_yaml": workflow_yaml,
                        "skills": [_skill_payload(skill) for skill in skills],
                        "tools": [_tool_payload(tool) for tool in tools],
                    },
                    ensure_ascii=False,
                ),
            },
        ]
        if self.settings.structured_output:
            output = model.with_structured_output(WorkflowReviewOutput).invoke(messages)
        else:
            output = model.invoke(messages)
        try:
            review = coerce_model_output(output, WorkflowReviewOutput)
        except Exception as exc:
            return WorkflowReviewOutput(
                approved=False,
                issues=[f"reviewer returned invalid structured output: {exc}"],
                corrected_workflow_yaml="",
            )
        review.corrected_workflow_yaml = _strip_fenced_block(review.corrected_workflow_yaml)
        return review


def _planner_system_prompt() -> str:
    """内部方法：规划器系统提示词。
    
        返回:
            返回类型为 str 的结果
        """
    return """You are the ZGraph temporary workflow planner.

Return JSON only with this shape:
{"workflow_yaml": "...", "notes": "..."}

Build a deterministic workflow.yaml for the request. The executor, not you, will run
the tools. You must not include prose outside JSON.

Workflow YAML schema:
- name: string
- version: "1"
- mode: sequential
- steps: list
- each step has id, type: tool|noop, tool, needs, args, outputs, assert, retries

Rules:
- Use only tools listed in the input.
- Noop steps cannot produce outputs. If a dry-run step needs BANK_ID or another
  output, use a tool step with bash that echoes a JSON object.
- Every relative date must be obtained by the datetime tool.
- Never use shell date commands such as date, date +%s, or Get-Date, even for
  dry-run IDs. Use fixed placeholder IDs or a prior datetime step output.
- Use strict sequential dependencies. A step that consumes a value must list the
  producing step in needs.
- Store IDs with outputs, for example BANK_ID: json.data.id.
- Output expressions may only be one of: content, ok, data.*, json.*, or $.*.
- Assertions may only reference declared output variables, state.*, or steps.*.
- To check a JSON code, first capture it, for example:
  outputs:
    BANK_CODE: json.code
    BANK_ID: json.data.id
  assert:
    - BANK_CODE == 0
    - BANK_ID exists
- Never write unsupported assertions such as "code == 0" or "data.id exists".
- If the user did not provide required business fields, build a workflow that fails
  early with a noop assertion rather than guessing values.
- For shell commands, keep environment variables as $ZBZN_BASE_URL and $ZBZN_API_KEY.
"""


def _reviewer_system_prompt() -> str:
    """内部方法：审核器系统提示词。
    
        返回:
            返回类型为 str 的结果
        """
    return """You are the ZGraph temporary workflow reviewer.

Return JSON only with this shape:
{"approved": true, "issues": [], "corrected_workflow_yaml": ""}

Review only the YAML. Do not execute tools. Reject the workflow if:
- a step uses a tool not in the provided tools list
- a noop step declares outputs
- dependencies are missing or out of order
- a later step consumes an ID without declaring needs
- relative dates are guessed instead of using datetime
- shell date commands such as date, date +%s, or Get-Date appear anywhere
- output expressions are not content, ok, data.*, json.*, or $.*
- assertions reference undeclared values such as code or data.id instead of
  declared output variables such as BANK_CODE or BANK_ID
- required outputs like BANK_ID, QUESTION_ID, PAPER_ID are not asserted
- the workflow appears to skip a required business step

If the workflow is almost correct, put a corrected full YAML document in
corrected_workflow_yaml and set approved to true. Otherwise set approved to false
and list concise issues.
"""


def _skill_payload(skill: Skill) -> dict[str, Any]:
    """内部方法：技能载荷。
    
        参数:
            skill: 技能（Skill）
    
        返回:
            返回类型为 dict[str, Any] 的结果
        """
    return {
        "name": skill.name,
        "description": skill.description,
        "required_tools": skill.required_tools,
        "validations": skill.validations,
        "tags": skill.tags,
        "content": skill.content[:12000],
    }


def _tool_payload(tool: RuntimeTool) -> dict[str, Any]:
    """内部方法：工具载荷。
    
        参数:
            tool: 工具（RuntimeTool）
    
        返回:
            返回类型为 dict[str, Any] 的结果
        """
    schema: dict[str, Any] | None = None
    if tool.args_schema is not None:
        try:
            schema = tool.args_schema.model_json_schema()
        except Exception:
            schema = None
    return {
        "name": tool.name,
        "description": tool.description,
        "risk_level": tool.risk_level,
        "args_schema": schema,
    }


def _strip_fenced_block(text: str) -> str:
    """内部方法：stripfenced阻塞。
    
        参数:
            text: 文本（str）
    
        返回:
            返回类型为 str 的结果
        """
    stripped = (text or "").strip()
    match = re.fullmatch(r"```(?:yaml|yml|json)?\s*(.*?)```", stripped, flags=re.DOTALL | re.IGNORECASE)
    if match:
        return match.group(1).strip()
    return stripped


def _offline_unavailable_workflow() -> str:
    """内部方法：offlineunavailable工作流。
    
        返回:
            返回类型为 str 的结果
        """
    return """name: workflow_planning_unavailable
version: "1"
mode: sequential
steps:
  - id: planning_unavailable
    type: noop
    assert:
      - state.workflow_planner_available == true
"""
