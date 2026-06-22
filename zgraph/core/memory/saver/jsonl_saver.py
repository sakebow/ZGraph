from __future__ import annotations

import json
from pathlib import Path
from typing import Any


class JsonlMemorySaver:

    """jsonl记忆保存器。"""
    def __init__(self, path: Path) -> None:
        """初始化实例属性。
        
            参数:
                path: 路径（Path）
            """

        self.path = path

    def save(self, record: dict[str, Any]) -> None:
        """保存。
        
            参数:
                record: 记录（dict[str, Any]）
            """

        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
