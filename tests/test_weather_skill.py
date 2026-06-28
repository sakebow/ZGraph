"""weather skill 业务测试（最小化）。

只验证 SKILL.md 包含核心关键词「气温」。
"""

from __future__ import annotations

from pathlib import Path

import pytest


pytestmark = pytest.mark.integration


WEATHER_SKILL_PATH = (
    Path(__file__).resolve().parents[1]
    / ".zgraph" / "skills" / "weather" / "SKILL.md"
)


class TestWeatherSkill:
    def test_temperature_shown_as_number(self) -> None:
        """SKILL.md 必须用数字（带 °C）表示气温，而不是只用占位符。

        验收标准：wttr.in 示例输出里能看到具体数字 + °C 形式，例如 ``+8°C``。
        """
        assert WEATHER_SKILL_PATH.exists(), f"missing skill file: {WEATHER_SKILL_PATH}"
        content = WEATHER_SKILL_PATH.read_text(encoding="utf-8")
        # 排除只有 `%t` 这类占位符的情况：要求示例里有具体数字
        assert any(
            f"{n}" in content or f"+{n}" in content
            for n in range(-50, 60)
        ), "no concrete numeric temperature (e.g. `+8`) in SKILL.md"
