from __future__ import annotations

from typing import Any, Literal
from typing_extensions import NotRequired

from zgraph.core.state.base import BaseState


class AgentState(BaseState, total=False):
    """智能体状态。继承自 BaseState"""

    user_input: str
    messages: list[dict[str, Any]]
    hint: dict[str, Any]
    intent: dict[str, Any]
    todo: list[dict[str, Any]]
    capabilities: dict[str, Any]
    status: Literal["created", "running", "interrupted", "completed", "failed"]
    output: str
    error: NotRequired[str]
