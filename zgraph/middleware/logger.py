from __future__ import annotations

import logging
import time
from typing import Any

from zgraph.middleware.base import NextHandler


class LoggerMiddleware:

    """logger中间件。"""
    def __init__(self, logger: logging.Logger | None = None) -> None:
        self.logger = logger or logging.getLogger("zgraph")

    def handle(self, request: dict[str, Any], call_next: NextHandler) -> dict[str, Any]:
        started = time.perf_counter()
        response = call_next(request)
        elapsed_ms = (time.perf_counter() - started) * 1000
        self.logger.info(
            "run_id=%s status=%s elapsed_ms=%.2f",
            response.get("run_id"),
            response.get("status"),
            elapsed_ms,
        )
        return response
