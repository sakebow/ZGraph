from __future__ import annotations

from typing import Any

from zgraph.workflow.base import WorkflowResult


class ValidateWorkflow:

    """校验工作流。"""
    name = "guardian.validate"

    def run(self, state: dict[str, Any]) -> WorkflowResult:
        errors: list[str] = []
        for key in ("hint", "intent", "todo"):
            if not state.get(key):
                errors.append(f"missing {key}")
        capabilities = state.get("capabilities") or {}
        for key in ("selected_tools", "required_tools", "risk_level", "retrieval_strategy"):
            if key not in capabilities:
                errors.append(f"missing capabilities.{key}")
        status = "failed" if errors else "completed"
        return WorkflowResult(self.name, status, {"valid": not errors}, errors)
