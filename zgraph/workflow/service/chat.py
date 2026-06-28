from __future__ import annotations

from typing import Any

from zgraph.workflow.base import WorkflowResult


class ChatWorkflow:

    """chat工作流。"""
    name = "chat"

    def run(self, state: dict[str, Any]) -> WorkflowResult:
        return WorkflowResult(
            self.name,
            "completed",
            {
                "messages": [
                    {"role": "system", "content": state.get("system_prompt", "")},
                    {"role": "user", "content": state.get("user_input", "")},
                ]
            },
        )
