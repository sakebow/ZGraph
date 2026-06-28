from __future__ import annotations

from typing import Any, Protocol

# 适配器接口
class Adapter(Protocol):

    def parse(self, payload: Any) -> str:
        ...

    def format(self, content: str, *, metadata: dict[str, Any] | None = None) -> Any:
        ...
