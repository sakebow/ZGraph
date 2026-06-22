from __future__ import annotations

import traceback
from typing import Any

from zgraph.middleware.base import NextHandler


class ExceptionMiddleware:

    """异常中间件。"""
    def __init__(self, debug: bool = False) -> None:
        self.debug = debug

    def handle(self, request: dict[str, Any], call_next: NextHandler) -> dict[str, Any]:
        try:
            return call_next(request)
        except Exception as exc:
            payload = {
                "status": "failed",
                "run_id": request.get("run_id"),
                "error": str(exc),
            }
            if self.debug:
                payload["traceback"] = traceback.format_exc()
            return payload
