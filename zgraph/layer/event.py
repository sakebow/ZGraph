from __future__ import annotations

from typing import Any


class ApprovalEventLayer:

    """审批事件层。"""
    name = "event.approval"

    def handle(self, payload: dict[str, Any]) -> dict[str, Any]:
        return {
            "type": "approval",
            "approved": bool(payload.get("approved")),
            "reason": payload.get("reason", ""),
            "payload": payload,
        }


class InterruptEventLayer:

    """中断事件层。"""
    name = "event.interrupt"

    def handle(self, payload: dict[str, Any]) -> dict[str, Any]:
        return {
            "type": "interrupt",
            "interrupt": payload.get("interrupt", payload),
        }
