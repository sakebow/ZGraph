"""main.py ``_parse_last_event_id`` 单元测试。

SSE 续传协议要求 client 在重连时带 Last-Event-ID header，server 从下一个
chunk 开始发。当前实现仅跳过序号，不做跨 completion_id 的 replay buffer。
"""

from __future__ import annotations

import pytest

from main import _parse_last_event_id


pytestmark = pytest.mark.integration


class TestParseLastEventId:
    @pytest.mark.parametrize(
        "header,expected",
        [
            # 无 header / 空 header：不跳过
            (None, 0),
            ("", 0),
            # 正常格式
            ("chatcmpl-abc123def456-1", 1),
            ("chatcmpl-abc123def456-7", 7),
            ("chatcmpl-xyz-100", 100),
            # 0 视为不跳过
            ("chatcmpl-abc-0", 0),
            # 异常格式：降级为不跳过
            ("not-a-valid-id", 0),
            ("chatcmpl-abc-", 0),         # 尾部空
            ("chatcmpl-abc-xyz", 0),      # 尾部非数字
            ("chatcmpl-abc", 0),          # 没有序号
            # 边界：多个 "-" 时 rpartition 只取最后一个
            ("chatcmpl-foo-bar-baz-3", 3),
            ("chatcmpl-abc--1", 1),       # 双 dash：rpartition 仍能拿到最后一段
        ],
    )
    def test_parse(self, header: str | None, expected: int) -> None:
        assert _parse_last_event_id(header) == expected
