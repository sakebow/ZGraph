from __future__ import annotations

import json
import re
from typing import Any

from pydantic import BaseModel, Field

from zgraph.config import Settings
from zgraph.core.memory.loader import MemoryLoader
from zgraph.core.provider import build_chat_model_with_fallback
from zgraph.workflow.base import WorkflowResult
from zgraph.workflow.service.structured import coerce_model_output


class RecommendedQuestion(BaseModel):

    """recommendedquestion。继承自 BaseModel。"""
    message: str = Field(description="A concise recommended follow-up question.")


class RecommendedQuestionsOutput(BaseModel):

    """recommendedquestions输出。继承自 BaseModel。"""
    data: list[RecommendedQuestion]


RECOMMEND_SYSTEM_PROMPT = """You recommend useful follow-up questions.

Use the latest saved conversation message as context. Return only the structured schema:
{"data": [{"message": "..."}]}

Rules:
- Produce 3 concise questions.
- Each message must be actionable and specific.
- Never output literal placeholders such as "...", "question", or "TODO".
- Do not include explanations outside the schema.
"""


class RecommendQuestionsWorkflow:
    """推荐questions工作流。"""
    name = "recommend_questions"

    def __init__(self, settings: Settings, memory_loader: MemoryLoader) -> None:
        self.settings = settings
        self.memory_loader = memory_loader

    def run(self, state: dict[str, Any]) -> WorkflowResult:
        latest_message = _clean_latest_message(self.memory_loader.load_latest_message())
        if not latest_message:
            return WorkflowResult(self.name, "completed", {"data": []})

        if not self.settings.offline and self.settings.api_key:
            try:
                output = self._run_llm(latest_message)
                return WorkflowResult(self.name, "completed", output.model_dump())
            except Exception:
                return WorkflowResult(self.name, "completed", self._fallback(latest_message).model_dump())

        return WorkflowResult(self.name, "completed", self._fallback(latest_message).model_dump())

    def _run_llm(self, latest_message: str) -> RecommendedQuestionsOutput:
        model = build_chat_model_with_fallback(self.settings)
        messages = [
            {"role": "system", "content": RECOMMEND_SYSTEM_PROMPT},
            {"role": "user", "content": f"Latest saved message:\n{latest_message}"},
        ]
        if self.settings.structured_output:
            try:
                structured_model = model.with_structured_output(RecommendedQuestionsOutput)
                output = coerce_model_output(structured_model.invoke(messages), RecommendedQuestionsOutput)
                _ensure_useful(output)
                return output
            except Exception:
                pass

        json_messages = [
            {
                "role": "system",
                "content": RECOMMEND_SYSTEM_PROMPT
                + "\nReturn a single strict JSON object matching the schema. No markdown.",
            },
            {"role": "user", "content": f"Latest saved message:\n{latest_message}"},
        ]
        output = coerce_model_output(model.invoke(json_messages), RecommendedQuestionsOutput)
        _ensure_useful(output)
        return output

    def _fallback(self, latest_message: str) -> RecommendedQuestionsOutput:
        brief = " ".join(latest_message.strip().split())[:120]
        if not brief:
            brief = "the last response"
        return RecommendedQuestionsOutput(
            data=[
                RecommendedQuestion(message=f"What should I do next based on: {brief}?"),
                RecommendedQuestion(message=f"Can you expand the most important point in: {brief}?"),
                RecommendedQuestion(message=f"What risks or missing details should I check after: {brief}?"),
            ]
        )

    @staticmethod
    def dumps(data: dict[str, Any]) -> str:
        return json.dumps(data, ensure_ascii=False)


def _clean_latest_message(message: str) -> str:
    cleaned = re.sub(r"<think>.*?</think>", "", message, flags=re.DOTALL | re.IGNORECASE)
    return " ".join(cleaned.strip().split())


def _ensure_useful(output: RecommendedQuestionsOutput) -> None:
    if len(output.data) < 1:
        raise ValueError("Recommendation output is empty")
    for item in output.data:
        text = item.message.strip()
        normalized = text.lower().strip(". !?")
        if len(text) < 8 or normalized in {"", "question", "todo"} or text in {"...", "…"}:
            raise ValueError("Recommendation output contains placeholder content")
