from __future__ import annotations

from typing import Any, Literal

from zgraph.core.state.base import BaseState


class InterruptState(BaseState, total=False):
    """中断状态。继承自 BaseState。"""

    interrupt_id: str
    reason: str
    risk_level: Literal["low", "medium", "high"]
    requested_action: dict[str, Any]
    status: Literal["pending", "approved", "refused", "expired"]
    decision_by: str
    decision_reason: str
