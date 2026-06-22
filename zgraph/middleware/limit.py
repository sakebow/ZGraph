from __future__ import annotations

import threading
import time
from collections import deque
from typing import Any

from zgraph.middleware.base import NextHandler


class RateLimitMiddleware:
    """比率限制中间件。"""
    def __init__(self, max_calls: int = 60, period_seconds: int = 60) -> None:
        self.max_calls = max_calls
        self.period_seconds = period_seconds
        self.calls: deque[float] = deque()
        self.lock = threading.Lock()

    def handle(self, request: dict[str, Any], call_next: NextHandler) -> dict[str, Any]:
        now = time.time()
        with self.lock:
            while self.calls and now - self.calls[0] > self.period_seconds:
                self.calls.popleft()
            if len(self.calls) >= self.max_calls:
                return {
                    "status": "failed",
                    "error": "rate limit exceeded",
                    "run_id": request.get("run_id"),
                }
            self.calls.append(now)
        return call_next(request)
