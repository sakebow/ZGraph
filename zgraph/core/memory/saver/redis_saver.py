from __future__ import annotations

import json
from typing import Any


class RedisMemorySaver:

    """redis记忆保存器。"""
    def __init__(self, url: str, key: str = "zgraph:memory") -> None:
        """初始化实例属性。
        
            参数:
                url: URL（str）
                key: 键，默认为 'zgraph:memory'（str）
            """

        self.url = url
        self.key = key
        self._client: Any | None = None

    @property
    def client(self) -> Any:
        """客户端。
        
            返回:
                返回类型为 Any 的结果
            """

        if self._client is None:
            try:
                import redis
            except ImportError as exc:
                raise RuntimeError("redis package is not installed") from exc
            self._client = redis.Redis.from_url(self.url)
        return self._client

    def save(self, record: dict[str, Any]) -> None:
        """保存。
        
            参数:
                record: 记录（dict[str, Any]）
            """

        self.client.rpush(self.key, json.dumps(record, ensure_ascii=False))
