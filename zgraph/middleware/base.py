from __future__ import annotations

from typing import Any, Callable, Protocol


NextHandler = Callable[[dict[str, Any]], dict[str, Any]]


class Middleware(Protocol):

    """中间件。继承自 Protocol。"""
    def handle(self, request: dict[str, Any], call_next: NextHandler) -> dict[str, Any]:
        ...


class MiddlewareChain:

    """中间件chain。"""
    def __init__(self, middlewares: list[Middleware], endpoint: NextHandler) -> None:
        """初始化实例属性"""

        self.middlewares = middlewares
        self.endpoint = endpoint

    def __call__(self, request: dict[str, Any]) -> dict[str, Any]:
        def build(index: int) -> NextHandler:
            if index >= len(self.middlewares):
                return self.endpoint

            def _next(payload: dict[str, Any]) -> dict[str, Any]:
                return self.middlewares[index].handle(payload, build(index + 1))

            return _next

        return build(0)(request)
