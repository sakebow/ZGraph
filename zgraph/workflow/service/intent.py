from __future__ import annotations

import json
import logging
import re
import time
from typing import Any, Literal

from pydantic import BaseModel, Field

from zgraph.config import Settings
from zgraph.core.provider import build_chat_model_with_fallback
from zgraph.core.tokenizer.word import WordTokenizer
from zgraph.workflow.base import WorkflowResult
from zgraph.workflow.service.structured import coerce_model_output, extract_json_object


RiskLevel = Literal["low", "medium", "high"]
Difficulty = Literal["easy", "medium", "hard"]


class HintOutput(BaseModel):

    """hint输出。继承自 BaseModel。"""
    summary: str = Field(description="Brief semantic summary of the user request.")
    domain: str = Field(description="Primary domain of the task.")
    task_type: str = Field(description="Stable task type identifier.")
    keywords: list[str] = Field(description="Semantic keywords, not substring matches.")
    slots: dict[str, Any] = Field(default_factory=dict)
    candidate_workflows: list[str] = Field(default_factory=list)
    candidate_tools: list[str] = Field(default_factory=list)
    risk_signals: list[str] = Field(default_factory=list)


class IntentOutput(BaseModel):

    """意图输出。继承自 BaseModel。"""
    name: str = Field(description="Intent name.")
    confidence: float = Field(ge=0, le=1)
    difficulty: Difficulty
    risk_hint: RiskLevel


class TodoOutput(BaseModel):

    """todo输出。继承自 BaseModel。"""
    id: int
    item: str


class IntentWorkflowOutput(BaseModel):

    """意图工作流输出。继承自 BaseModel。"""
    hint: HintOutput
    intent: IntentOutput
    todo: list[TodoOutput]


LOW_TOOLS = {"read", "glob"}
MEDIUM_TOOLS = {"write", "update", "settodolist", "spawn"}
HIGH_TOOLS = {
    "delete",
    "bash",
    "http",
    "adapter.call",
}
ALL_TOOLS = LOW_TOOLS | MEDIUM_TOOLS | HIGH_TOOLS | {"approve-interrupt", "refuse-interrupt"}
ALL_RISKS = {"low", "medium", "high"}
ALL_DIFFICULTIES = {"easy", "medium", "hard"}
ALL_INTENTS = {"chat", "generate_document", "recommend_questions"}


INTENT_SYSTEM_PROMPT = """You are the ZGraph intent workflow.

Classify the user's request semantically and return only the structured schema.
Do not use substring matching. Do not infer high risk from short token overlap.

Risk policy:
- low: chat, question answering, summarization, read-only inspection, pure text.
- medium: writing files, updating state, generating artifacts, spawning drafts.
- high: deleting files, running shell/bash/scripts, permission changes, external HTTP/API side effects.

Special workflow:
- If the user asks for recommended questions, follow-up questions, or question suggestions,
  set intent.name to "recommend_questions", hint.task_type to "recommend_questions",
  candidate_workflows to ["recommend_questions"], candidate_tools to [], risk_hint to "low".

Candidate tools must come from:
read, glob, write, update, settodolist, spawn, delete, bash, http, adapter.call.
"""


class IntentWorkflow:
    """意图工作流。"""
    name = "intent"

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings
        self.logger = logging.getLogger("zgraph.intent")

    def run(self, state: dict[str, Any]) -> WorkflowResult:
        user_input = str(state.get("user_input", ""))
        if self.settings and not self.settings.offline and self.settings.api_key:
            try:
                output, source = self._run_llm(user_input)
                return WorkflowResult(self.name, "completed", self._normalize(output, source=source))
            except Exception as exc:
                fallback = self._fallback(user_input)
                data = self._normalize(fallback, source="local_fallback")
                data["intent_error"] = str(exc)
                return WorkflowResult(self.name, "completed", data)

        return WorkflowResult(self.name, "completed", self._normalize(self._fallback(user_input), source="local_fallback"))

    def _run_llm(self, user_input: str) -> tuple[IntentWorkflowOutput, str]:
        if self.settings is None:
            raise RuntimeError("settings are required for LLM intent workflow")
        model = build_chat_model_with_fallback(self.settings)
        messages = [
            {"role": "system", "content": INTENT_SYSTEM_PROMPT},
            {"role": "user", "content": user_input},
        ]
        if self.settings.structured_output:
            structured_model = model.with_structured_output(IntentWorkflowOutput)
            try:
                started = time.perf_counter()
                self.logger.info("stage=intent.llm_structured:start chars=%s", len(user_input))
                output = structured_model.invoke(messages)
                self.logger.info(
                    "stage=intent.llm_structured:end elapsed_ms=%.2f",
                    (time.perf_counter() - started) * 1000,
                )
                return coerce_model_output(output, IntentWorkflowOutput), "llm_structured"
            except Exception as exc:
                self.logger.warning("stage=intent.llm_structured:error error=%s fallback=llm_json", exc)

        json_messages = [
            {
                "role": "system",
                "content": INTENT_SYSTEM_PROMPT
                + "\nReturn a single strict JSON object matching the schema. No markdown.",
            },
            {"role": "user", "content": user_input},
        ]
        started = time.perf_counter()
        self.logger.info("stage=intent.llm_json:start chars=%s", len(user_input))
        raw_result = model.invoke(json_messages)
        self.logger.info(
            "stage=intent.llm_json:end elapsed_ms=%.2f",
            (time.perf_counter() - started) * 1000,
        )
        try:
            return coerce_model_output(raw_result, IntentWorkflowOutput), "llm_json"
        except Exception:
            return self._partial_from_model_output(raw_result, user_input), "llm_json_partial"

    def _partial_from_model_output(self, result: Any, user_input: str) -> IntentWorkflowOutput:
        content = getattr(result, "content", result)
        if isinstance(content, dict):
            payload = content
        elif isinstance(content, str):
            payload = json.loads(extract_json_object(content))
        elif isinstance(content, list):
            text = " ".join(str(item.get("text", item)) if isinstance(item, dict) else str(item) for item in content)
            payload = json.loads(extract_json_object(text))
        else:
            raise ValueError(f"Unsupported partial intent output: {type(result)!r}")

        intent_payload = payload.get("intent") if isinstance(payload.get("intent"), dict) else payload
        hint_payload = payload.get("hint") if isinstance(payload.get("hint"), dict) else {}

        task_type = str(
            hint_payload.get("task_type")
            or intent_payload.get("task_type")
            or payload.get("task_type")
            or "chat"
        ).strip().lower()
        name = str(intent_payload.get("name") or _intent_name_for_task_type(task_type)).strip().lower()
        risk = str(intent_payload.get("risk_hint") or intent_payload.get("risk") or "low").strip().lower()
        if risk not in ALL_RISKS:
            risk = "low"
        difficulty = str(intent_payload.get("difficulty") or _difficulty_for_risk(risk)).strip().lower()
        if difficulty not in ALL_DIFFICULTIES:
            difficulty = _difficulty_for_risk(risk)

        candidate_tools = hint_payload.get("candidate_tools") or intent_payload.get("candidate_tools") or []
        if not isinstance(candidate_tools, list):
            candidate_tools = []
        candidate_workflows = hint_payload.get("candidate_workflows") or [task_type]
        if not isinstance(candidate_workflows, list):
            candidate_workflows = [task_type]
        if risk in {"medium", "high"} and "guardian" not in candidate_workflows:
            candidate_workflows.append("guardian")

        keywords = hint_payload.get("keywords") or intent_payload.get("keywords") or sorted(_tokens(user_input))[:20]
        if not isinstance(keywords, list):
            keywords = sorted(_tokens(user_input))[:20]

        return IntentWorkflowOutput(
            hint=HintOutput(
                summary=str(hint_payload.get("summary") or payload.get("summary") or user_input[:220]),
                domain=str(hint_payload.get("domain") or payload.get("domain") or "agent_runtime_design"),
                task_type=task_type,
                keywords=[str(item) for item in keywords],
                slots=hint_payload.get("slots") if isinstance(hint_payload.get("slots"), dict) else {},
                candidate_workflows=[str(item) for item in candidate_workflows],
                candidate_tools=[str(item) for item in candidate_tools],
                risk_signals=hint_payload.get("risk_signals") if isinstance(hint_payload.get("risk_signals"), list) else [],
            ),
            intent=IntentOutput(
                name=name,
                confidence=float(intent_payload.get("confidence") or 0.8),
                difficulty=difficulty,  # type: ignore[arg-type]
                risk_hint=risk,  # type: ignore[arg-type]
            ),
            todo=[
                TodoOutput(id=1, item="Build structured intent output"),
                TodoOutput(id=2, item="Compile required capabilities"),
                TodoOutput(id=3, item="Run guardian workflow when required"),
                TodoOutput(id=4, item="Execute selected workflow or agent"),
            ],
        )

    def _fallback(self, user_input: str) -> IntentWorkflowOutput:
        tokens = _tokens(user_input)
        risk: RiskLevel = "low"
        if tokens & {"delete", "remove", "bash", "shell", "execute", "script", "permission", "chmod", "http", "api"}:
            risk = "high"
        elif tokens & {"write", "create", "generate", "update", "modify", "save", "merge", "compose", "implement"}:
            risk = "medium"

        task_type = "chat"
        intent_name = "chat"
        if (tokens & {"recommend", "recommendations", "suggest", "suggestions"}) and (
            tokens & {"question", "questions", "followup", "follow-up", "next"}
        ):
            task_type = "recommend_questions"
            intent_name = "recommend_questions"
            risk = "low"
        elif "merge" in tokens:
            task_type = "document_merge"
            intent_name = "generate_document"
        elif risk in {"medium", "high"}:
            task_type = "file_generation"
            intent_name = "generate_document"
        elif "summarize" in tokens or "summary" in tokens:
            task_type = "summary"
            intent_name = "chat"

        candidate_tools = self._fallback_tools(tokens, risk, task_type)
        candidate_workflows = [task_type]
        if risk in {"medium", "high"}:
            candidate_workflows.append("guardian")

        difficulty: Difficulty = "easy"
        if risk == "medium" or len(user_input) > 500:
            difficulty = "medium"
        if risk == "high" or len(user_input) > 1500:
            difficulty = "hard"

        return IntentWorkflowOutput(
            hint=HintOutput(
                summary=user_input[:220],
                domain="agent_runtime_design",
                task_type=task_type,
                keywords=sorted(tokens)[:20],
                slots={
                    "input_files": re.findall(r"[\w./\\-]+\.(?:md|txt|json|yaml|yml|py)", user_input),
                    "output_format": "markdown" if "markdown" in tokens else "text",
                },
                candidate_workflows=candidate_workflows,
                candidate_tools=candidate_tools,
                risk_signals=candidate_tools if risk != "low" else [],
            ),
            intent=IntentOutput(
                name=intent_name,
                confidence=0.72 if user_input else 0.0,
                difficulty=difficulty,
                risk_hint=risk,
            ),
            todo=[
                TodoOutput(id=1, item="Build structured intent output"),
                TodoOutput(id=2, item="Compile required capabilities"),
                TodoOutput(id=3, item="Run guardian workflow when required"),
                TodoOutput(id=4, item="Execute selected workflow or agent"),
            ],
        )

    def _fallback_tools(self, tokens: set[str], risk: RiskLevel, task_type: str) -> list[str]:
        if task_type == "recommend_questions":
            return []
        tools: list[str] = []
        if tokens & {"read", "inspect", "open", "file", "files", "list", "glob"}:
            tools.append("read")
        if tokens & {"list", "glob", "find", "search"}:
            tools.append("glob")
        if risk in {"medium", "high"}:
            tools.append("write")
        if tokens & {"update", "modify", "replace"}:
            tools.append("update")
        if tokens & {"delete", "remove"}:
            tools.append("delete")
        if tokens & {"bash", "shell", "execute", "script", "run"}:
            tools.append("bash")
        if tokens & {"http", "api", "request", "post"}:
            tools.append("http")
        if not tools:
            tools.append("read")
        return list(dict.fromkeys(tools))

    def _normalize(self, output: IntentWorkflowOutput, *, source: str) -> dict[str, Any]:
        data = output.model_dump()
        hint = data["hint"]
        intent = data["intent"]
        if not data.get("todo"):
            data["todo"] = [
                {"id": 1, "item": "Build structured intent output"},
                {"id": 2, "item": "Compile required capabilities"},
                {"id": 3, "item": "Run guardian workflow when required"},
                {"id": 4, "item": "Execute selected workflow or agent"},
            ]

        risk = str(intent.get("risk_hint", "low")).lower()
        if risk not in ALL_RISKS:
            risk = "low"
        intent["risk_hint"] = risk

        difficulty = str(intent.get("difficulty", "easy")).lower()
        if difficulty not in ALL_DIFFICULTIES:
            difficulty = "easy"
        intent["difficulty"] = difficulty

        name = str(intent.get("name", "chat")).strip().lower()
        if name not in ALL_INTENTS:
            name = _intent_name_for_task_type(str(hint.get("task_type", "chat")).strip().lower())
        intent["name"] = name

        hint["candidate_tools"] = [
            tool for tool in hint.get("candidate_tools", []) if str(tool).strip().lower() in ALL_TOOLS
        ]
        hint["candidate_tools"] = list(dict.fromkeys(str(tool).strip().lower() for tool in hint["candidate_tools"]))
        hint["candidate_workflows"] = list(
            dict.fromkeys(str(workflow).strip().lower() for workflow in hint.get("candidate_workflows", []))
        )
        hint["risk_signals"] = list(dict.fromkeys(str(signal) for signal in hint.get("risk_signals", [])))
        hint["source"] = source
        return data


def _tokens(text: str) -> set[str]:
    return WordTokenizer().tokenize(text)


def _intent_name_for_task_type(task_type: str) -> str:
    if task_type == "recommend_questions":
        return "recommend_questions"
    if task_type in {"file_generation", "document_merge"}:
        return "generate_document"
    return "chat"


def _difficulty_for_risk(risk: str) -> Difficulty:
    if risk == "high":
        return "hard"
    if risk == "medium":
        return "medium"
    return "easy"
