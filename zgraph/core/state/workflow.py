from __future__ import annotations

from typing import Any, Literal

from zgraph.core.state.base import BaseState


class WorkflowState(BaseState, total=False):
    """工作流状态。继承自 BaseState。"""

    workflow_name: str
    step: str
    status: Literal["pending", "running", "completed", "failed", "skipped"]
    input: dict[str, Any]
    output: dict[str, Any]
    errors: list[str]
