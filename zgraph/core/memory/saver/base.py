from __future__ import annotations

from typing import Any, Protocol


class BaseMemorySaver(Protocol):

    """base记忆保存器。继承自 Protocol。"""
    def save(self, record: dict[str, Any]) -> None:

        """保存。
        
            参数:
                record: 记录（dict[str, Any]）
            """
        ...
