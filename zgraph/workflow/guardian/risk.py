from __future__ import annotations

from typing import Any

from zgraph.workflow.base import WorkflowResult


RANK = {"low": 0, "medium": 1, "high": 2}


class RiskWorkflow:

    """风险工作流。"""
    name = "guardian.risk"

    def run(self, state: dict[str, Any]) -> WorkflowResult:
        intent = state.get("intent") or {}
        capabilities = state.get("capabilities") or {}
        risk = str(capabilities.get("risk_level") or intent.get("risk_hint") or "low")
        selected_tools = set(capabilities.get("selected_tools") or [])
        if selected_tools & {"bash", "delete", "http", "adapter.call"}:
            risk = "high"
        elif selected_tools & {"write", "update", "settodolist", "spawn"} and RANK.get(risk, 0) < 1:
            risk = "medium"
        return WorkflowResult(self.name, "completed", {"risk_level": risk, "selected_tools": sorted(selected_tools)})
