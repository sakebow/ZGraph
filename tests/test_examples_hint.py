"""Tests for examples discovery + system hint injection.

覆盖两个层面：
1. ``ZGraphRuntime.list_available_examples()`` / ``build_examples_hint()`` 扫描
   ``<zgraph_home>/storage/examples/`` 并产出可注入到 LLM 上下文的文本。
2. ``CompletionsInputLayer.handle(payload, system_hint=...)`` 在 hint 非空时把
   ``system: ...`` 行拼到 prompt 最前；hint 为空时与原来行为一致。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from zgraph.config import Settings
from zgraph.layer.input import CompletionsInputLayer
from zgraph.runtime import ZGraphRuntime


pytestmark = pytest.mark.integration


class TestListAvailableExamples:
    def test_returns_sorted_absolute_paths(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        """扫描 examples 目录，返回排序后的绝对路径列表。"""
        monkeypatch.setenv("ZGRAPH_HOME", str(tmp_path))
        monkeypatch.setenv("ZGRAPH_OFFLINE", "true")
        settings = Settings.from_env()
        examples = settings.zgraph_home / "storage" / "examples"
        examples.mkdir(parents=True)
        (examples / "b.png").write_bytes(b"\x89PNG\r\n\x1a\nB")
        (examples / "a.png").write_bytes(b"\x89PNG\r\n\x1a\nA")
        # 噪音：子目录不应被列出来
        (examples / "nested").mkdir()

        rt = ZGraphRuntime(settings)
        paths = rt.list_available_examples()

        assert len(paths) == 2
        assert all(p.endswith((".png",)) for p in paths)
        assert paths[0].endswith("a.png")
        assert paths[1].endswith("b.png")
        assert all(Path(p).is_absolute() for p in paths)

    def test_missing_dir_returns_empty(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        """examples 目录不存在时返回空列表，不抛错。"""
        monkeypatch.setenv("ZGRAPH_HOME", str(tmp_path))
        monkeypatch.setenv("ZGRAPH_OFFLINE", "true")
        settings = Settings.from_env()

        rt = ZGraphRuntime(settings)
        assert rt.list_available_examples() == []

    def test_build_hint_includes_all_paths(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        """hint 文本以 header 行开头，下面每行一个绝对路径。"""
        monkeypatch.setenv("ZGRAPH_HOME", str(tmp_path))
        monkeypatch.setenv("ZGRAPH_OFFLINE", "true")
        settings = Settings.from_env()
        examples = settings.zgraph_home / "storage" / "examples"
        examples.mkdir(parents=True)
        (examples / "only.png").write_bytes(b"x")

        rt = ZGraphRuntime(settings)
        hint = rt.build_examples_hint()

        assert hint.startswith("Available example media files")
        assert "media_input" in hint  # 让 LLM 知道工具名
        assert "only.png" in hint

    def test_build_hint_empty_when_no_examples(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        """没有 example 时返回空串，调用方据此跳过注入。"""
        monkeypatch.setenv("ZGRAPH_HOME", str(tmp_path))
        monkeypatch.setenv("ZGRAPH_OFFLINE", "true")
        settings = Settings.from_env()

        rt = ZGraphRuntime(settings)
        assert rt.build_examples_hint() == ""

    def test_real_examples_dir_has_png(self, tmp_settings: Settings):
        """真实仓库的 examples 目录至少含一张 PNG（避免测试与 fixture 失同步）。"""
        rt = ZGraphRuntime(tmp_settings)
        paths = rt.list_available_examples()
        # tmp_settings 沿用 conftest 的 fixture，可能指向真实 zgraph_home
        # （如果 monkeypatch 没改 ZGRAPH_HOME），所以这里只断言「至少一张 png」
        # 而不锁文件名。
        assert paths, "examples dir is empty — fixture/out-of-band cleanup?"
        assert any(p.lower().endswith(".png") for p in paths), (
            f"no PNG in examples: {paths}"
        )


class TestCompletionsInputLayerSystemHint:
    def test_no_hint_keeps_original_layout(self):
        """不传 hint 时，prompt 与改造前一致（保持向后兼容）。"""
        layer = CompletionsInputLayer()
        payload = {
            "messages": [
                {"role": "system", "content": "you are helpful"},
                {"role": "user", "content": "hello"},
            ]
        }
        parsed = layer.handle(payload)

        assert parsed["prompt"] == "system: you are helpful\nuser: hello"

    def test_hint_prepended_before_user_messages(self):
        """传 hint 时，system 行在最前面，原 messages 顺序保留。"""
        layer = CompletionsInputLayer()
        payload = {
            "messages": [
                {"role": "user", "content": "send me an image"},
            ]
        }
        hint = "Available example media files (use media_input tool to attach):\n- /tmp/a.png"
        parsed = layer.handle(payload, system_hint=hint)

        # hint 自身可能含换行（多行列表），但第一行必须是 system: ...
        lines = parsed["prompt"].split("\n")
        assert lines[0] == f"system: {hint.splitlines()[0]}"
        # 后续每行是原 hint 的剩余行
        assert lines[1] == "- /tmp/a.png"
        # 最后一段是 user message
        assert lines[-1] == "user: send me an image"

    def test_empty_hint_does_not_insert_blank_system_line(self):
        """hint 为空串时，绝不在 prompt 里塞空 system 行（避免污染 LLM 上下文）。"""
        layer = CompletionsInputLayer()
        payload = {"messages": [{"role": "user", "content": "hi"}]}
        parsed = layer.handle(payload, system_hint="")

        assert parsed["prompt"] == "user: hi"
        assert "system:" not in parsed["prompt"]

    def test_hint_preserves_messages_in_order(self):
        """hint 注入后，多轮 messages 仍按原顺序排列。"""
        layer = CompletionsInputLayer()
        payload = {
            "messages": [
                {"role": "system", "content": "be brief"},
                {"role": "user", "content": "q1"},
                {"role": "assistant", "content": "a1"},
                {"role": "user", "content": "q2"},
            ]
        }
        parsed = layer.handle(payload, system_hint="HINT")

        expected = (
            "system: HINT\n"
            "system: be brief\n"
            "user: q1\n"
            "assistant: a1\n"
            "user: q2"
        )
        assert parsed["prompt"] == expected
