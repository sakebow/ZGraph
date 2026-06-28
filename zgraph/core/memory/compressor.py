from __future__ import annotations

from typing import Any


class MemoryCompressor:

    """记忆压缩器。"""
    def compress(self, messages: list[dict[str, Any]], *, max_chars: int = 1200) -> str:
        """压缩。
        
            参数:
                messages: 消息（list[dict[str, Any]]）
        
            返回:
                返回类型为 str 的结果
            """
        chunks: list[str] = []
        for message in messages[-8:]:
            role = message.get("role", "unknown")
            content = str(message.get("content", ""))
            if content:
                chunks.append(f"{role}: {content}")
        summary = "\n".join(chunks)
        return summary[:max_chars]
