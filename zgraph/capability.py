from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from zgraph.config import Settings
from zgraph.core.register import Registry
from zgraph.core.skills.loader import Skill
from zgraph.core.tool.retriever import ToolRetriever
from zgraph.core.skills.researcher import SkillResearcher
from zgraph.core.tokenizer.service import build_tokenizer


RISK_RANK = {"low": 0, "medium": 1, "high": 2}


def _max_risk(values: list[str]) -> str:
    best = "low"
    for value in values:
        if RISK_RANK.get(value, 0) > RISK_RANK[best]:
            best = value
    return best


@dataclass(slots=True)
class CapabilityCompiler:

    settings: Settings
    tool_registry: Registry
    skills: list[Skill]

    def compile(self, state: dict[str, Any]) -> dict[str, Any]:
        hint = state.get("hint") or {}
        intent = state.get("intent") or {}
        query = " ".join(
            [
                str(state.get("user_input", "")),
                str(hint.get("summary", "")),
                " ".join(hint.get("keywords") or []),
                str(intent.get("name", "")),
            ]
        )
        tokenizer = build_tokenizer(self.settings)

        skill_researcher = SkillResearcher(self.skills, tokenizer)
        if self.settings.skill_search:
            skill_matches = skill_researcher.search(
                query,
                top_k=self.settings.skill_top_k,
                min_score=self.settings.skill_min_score,
            )
            selected_skills = [match.skill for match in skill_matches]
        else:
            selected_skills = self.skills

        candidate_tool_names = list(hint.get("candidate_tools") or [])
        required_tool_names: list[str] = []
        preconditions: list[str] = []
        validations: list[str] = []
        for skill in selected_skills:
            required_tool_names.extend(skill.required_tools)
            preconditions.extend(skill.preconditions)
            validations.extend(skill.validations)

        tool_retriever = ToolRetriever(self.tool_registry, tokenizer)
        tool_matches = tool_retriever.search(
            query,
            top_k=self.settings.tool_top_k,
            min_score=self.settings.tool_min_score,
        )
        selected_names = [match.tool.name for match in tool_matches]
        selected_names.extend(candidate_tool_names)
        selected_names.extend(required_tool_names)

        deduped_names: list[str] = []
        for name in selected_names:
            if self.tool_registry.get(name) is not None and name not in deduped_names:
                deduped_names.append(name)

        if not deduped_names:
            deduped_names = ["read"]

        selected_tools = tool_retriever.by_names(deduped_names)
        tool_risks = [tool.risk_level for tool in selected_tools]
        risk_level = _max_risk([str(intent.get("risk_hint", "low")), *tool_risks])
        difficulty = str(intent.get("difficulty", "easy"))
        strong_side_effects = bool(
            {"delete", "bash", "http", "adapter.call"} & set(deduped_names)
        )
        spawn_required = difficulty == "hard" or risk_level == "high" or strong_side_effects

        selected_workflows = list(hint.get("candidate_workflows") or [])
        if _requires_temporary_workflow(selected_skills) and "temporary_workflow" not in selected_workflows:
            selected_workflows.append("temporary_workflow")
        if risk_level in {"medium", "high"} and "guardian" not in selected_workflows:
            selected_workflows.append("guardian")

        return {
            "selected_skills": [skill.name for skill in selected_skills],
            "selected_tools": deduped_names,
            "required_tools": list(dict.fromkeys(required_tool_names)),
            "selected_workflows": list(dict.fromkeys(selected_workflows)),
            "preconditions": list(dict.fromkeys(preconditions)),
            "validations": list(dict.fromkeys(validations)),
            "risk_level": risk_level,
            "spawn_required": spawn_required,
            "retrieval_strategy": tokenizer.name,
        }


def _requires_temporary_workflow(skills: list[Skill]) -> bool:
    for skill in skills:
        tags = {tag.strip().lower() for tag in skill.tags}
        validations = {validation.strip().lower() for validation in skill.validations}
        if skill.workflow.strip() or skill.workflow_mode.strip().lower() == "strict":
            return True
        if "workflow" in tags or "strict-workflow" in tags:
            return True
        if "strict-workflow" in validations:
            return True
    return False
