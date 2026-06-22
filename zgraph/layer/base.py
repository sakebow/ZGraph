from __future__ import annotations

from typing import Any, Protocol


class Layer(Protocol):
    name: str

    def handle(self, payload: Any) -> Any:
        ...
