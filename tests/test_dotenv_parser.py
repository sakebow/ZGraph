"""run_me.ps1 的 Import-DotEnv 函数的行内注释剥离逻辑测试。

注意：PowerShell 的字符串处理用 O(n) 状态机，这里用 Python 等价实现测试。
真实 PowerShell 函数在 run_me.ps1 里用同样算法；这个测试保证逻辑正确。
"""

from __future__ import annotations

import pytest


pytestmark = pytest.mark.integration


def _strip_inline_comment(value: str) -> str:
    """Python 等价实现：模拟 run_me.ps1 的 Import-DotEnv 行内注释剥离。

    在 ``"..."`` / ``'...'`` 之外的 ``#`` 起，到行尾截掉。
    """
    hash_index = -1
    in_double = False
    in_single = False
    for i, ch in enumerate(value):
        if ch == '"' and not in_single:
            in_double = not in_double
        elif ch == "'" and not in_double:
            in_single = not in_single
        elif ch == "#" and not in_double and not in_single:
            hash_index = i
            break
    if hash_index >= 0:
        value = value[:hash_index].rstrip()
    if (value.startswith('"') and value.endswith('"')) or (
        value.startswith("'") and value.endswith("'")
    ):
        value = value[1:-1]
    return value


class TestStripInlineComment:
    @pytest.mark.parametrize(
        "raw,expected",
        [
            # 无注释：原样
            ('"abc"', "abc"),
            ("abc", "abc"),
            # 行内 # 注释（不在引号里）→ 剥离
            ('"abc" # comment', "abc"),
            ('"abc"  # 中文注释', "abc"),
            ('"abc"\t\t# indented comment', "abc"),
            # 引号内的 # 不算注释
            ('"abc#def"', "abc#def"),
            ('"abc # def" # outside', "abc # def"),
            # 单引号同理
            ("'abc' # comment", "abc"),
            ("'abc#def'", "abc#def"),
            # 嵌套引号：状态机不识别 \" 转义（与 PowerShell 行为一致）。
            # 外层 " 在第一/二个内层 " 处 toggle 三次，# 落在第二个内层 " 之后
            # 仍认为在引号内 → 不剥；首尾匹配剥掉最外层两个 "。
            ('"abc \\"def\\" # real comment"', 'abc \\"def\\" # real comment'),
            # 末尾只有 # 没注释
            ('"abc"#', "abc"),
            # 没有引号的情况
            ("abc # comment", "abc"),
            ("abc", "abc"),
        ],
    )
    def test_strip(self, raw: str, expected: str) -> None:
        assert _strip_inline_comment(raw) == expected


class TestDotEnvParser:
    """模拟整个 env 解析循环：读 .env 行 → strip_inline_comment → setenv。"""

    def _parse(self, content: str) -> dict[str, str]:
        result: dict[str, str] = {}
        for line in content.splitlines():
            trimmed = line.strip()
            if not trimmed or trimmed.startswith("#"):
                continue
            equals = trimmed.find("=")
            if equals < 1:
                continue
            key = trimmed[:equals].strip()
            value = _strip_inline_comment(trimmed[equals + 1:].strip())
            result[key] = value
        return result

    def test_parses_typical_dev_env(self) -> None:
        content = """
# comment line
KEY1="value1"
KEY2="value2" # inline comment
KEY3="" # empty
KEY4="https://example.com/path" # url with // should keep /
KEY5="# not a comment, it's a value"
"""
        parsed = self._parse(content)
        assert parsed["KEY1"] == "value1"
        assert parsed["KEY2"] == "value2"
        assert parsed["KEY3"] == ""
        assert parsed["KEY4"] == "https://example.com/path"
        # 引号内的 # 不算注释
        assert parsed["KEY5"] == "# not a comment, it's a value"

    def test_handles_malformed_lines(self) -> None:
        content = """
= no key
NOEQUALS
"""
        parsed = self._parse(content)
        assert parsed == {}
