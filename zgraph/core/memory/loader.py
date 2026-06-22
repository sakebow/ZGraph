from __future__ import annotations

import json
from pathlib import Path
from typing import Any


class MemoryLoader:

    """记忆加载器。"""
    def __init__(self, path: Path) -> None:
        """初始化实例属性。"""
        self.path = path

    def load_recent(self, *, limit: int = 20) -> list[dict[str, Any]]:
        """加载recent"""
        if not self.path.exists():
            return []
        rows: list[dict[str, Any]] = []
        with self.path.open("r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        return rows[-limit:]

    def load_latest(self) -> dict[str, Any] | None:
        """加载latest"""
        rows = self.load_recent(limit=1)
        if not rows:
            return None
        return rows[-1]

    def load_latest_message(self) -> str:
        """加载latest消息"""
        latest = self.load_latest()
        if latest is None:
            return ""

        message = latest.get("latest_message")
        if isinstance(message, str) and message.strip():
            return message

        messages = latest.get("messages")
        if isinstance(messages, list):
            for item in reversed(messages):
                if not isinstance(item, dict):
                    continue
                content = item.get("content")
                if isinstance(content, str) and content.strip():
                    return content

        summary = latest.get("summary")
        if isinstance(summary, str):
            return summary
        return ""
