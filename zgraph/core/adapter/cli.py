from __future__ import annotations

from typing import Any


class CliAdapter:

    """cli适配器。"""
    def parse(self, payload: Any) -> str:
        if isinstance(payload, str):
            return payload
        if isinstance(payload, list):
            return " ".join(str(item) for item in payload)
        return str(payload)

    def format(self, content: str, *, metadata: dict[str, Any] | None = None) -> str:
        return content
