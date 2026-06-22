from __future__ import annotations

import uuid
from typing import Any

from zgraph.workflow.base import WorkflowResult


class ApproveWorkflow:

    """批准工作流。"""
    name = "guardian.approve"

    def run(self, state: dict[str, Any]) -> WorkflowResult:
        risk_level = str(state.get("risk_level") or state.get("capabilities", {}).get("risk_level") or "low")
        if risk_level == "low":
            return WorkflowResult(self.name, "completed", {"approved": True, "reason": "low risk"})
        if risk_level == "medium":
            return WorkflowResult(
                self.name,
                "completed",
                {"approved": True, "reason": "medium risk approved by guardian policy"},
            )

        interrupt_id = uuid.uuid4().hex
        interrupt = {
            "interrupt_id": interrupt_id,
            "reason": "high risk action requires explicit approval",
            "risk_level": risk_level,
            "requested_action": state.get("hint", {}),
            "status": "pending",
        }
        return WorkflowResult(
            self.name,
            "interrupted",
            {"approved": False, "interrupt": interrupt},
        )
