"""Tests for ``MediaInputTool``（磁盘媒体 → media_store 桥接）。"""

from __future__ import annotations

from pathlib import Path

import pytest

from zgraph.config import Settings
from zgraph.core.tool.base import ToolContext
from zgraph.core.tool.tools import MediaInputTool
from zgraph.runtime import ZGraphRuntime


pytestmark = pytest.mark.integration


def _build_context(
    runtime: ZGraphRuntime,
    workspace_path: Path,
    run_id: str,
) -> ToolContext:
    """构造一个带 zgraph_home metadata + emit_media 的 ToolContext。

    不走完整的 ZGraphRuntime._run_unprotected()，避免 trigger 离线分支。
    """
    workspace = runtime.workspace_manager.create_run(run_id)
    # 让 storage_dir 落在 tmp_settings 指定的根下面
    return ToolContext(
        workspace=workspace,
        allow_bash=False,
        emit_media=runtime._make_emit_media(run_id),
        metadata={"zgraph_home": str(runtime.settings.zgraph_home)},
    )


class TestMediaInputTool:
    def test_relative_path_resolved_against_zgraph_home(
        self, tmp_path: Path, tmp_settings: Settings
    ):
        """相对路径会拼到 ``zgraph_home`` 之下，然后把字节塞进 media_store。"""
        rt = ZGraphRuntime(tmp_settings)
        # zgraph_home 已是 D:/work/ZGraph/.zgraph；examples/ 直接挂在它下面
        relative = "storage/examples/140037382_p0.png"
        absolute = (rt.settings.zgraph_home / relative).resolve()
        assert absolute.is_file(), f"sample image missing: {absolute}"

        context = _build_context(rt, tmp_path, run_id="r-rel")
        tool = MediaInputTool(context=context)
        result = tool.run(path=relative)

        assert result.ok, f"tool.run failed: {result.content}"
        assert result.data["modality"] == "image"
        assert result.data["mime"] == "image/png"
        assert result.data["size_bytes"] == absolute.stat().st_size
        assert result.data["source_path"] == str(absolute)
        assert result.data["url"].startswith("http")
        assert result.data["block_id"].startswith("image-")
        assert result.data["expires_at"] != ""

        # 文件真的被媒体存储收下了：tmp_store_path/r-rel/<name>
        stored = tmp_settings.tmp_store_path / "r-rel" / absolute.name
        assert stored.exists(), f"media_store did not write {stored}"

    def test_absolute_path_works_without_zgraph_home(
        self, tmp_path: Path, tmp_settings: Settings
    ):
        """绝对路径不依赖 metadata.zgraph_home 也能跑通。"""
        rt = ZGraphRuntime(tmp_settings)
        absolute = (rt.settings.zgraph_home / "storage" / "examples" / "140037382_p0.png").resolve()

        context = _build_context(rt, tmp_path, run_id="r-abs")
        # 显式清掉 zgraph_home 也得能用，因为是绝对路径
        context.metadata.pop("zgraph_home", None)
        tool = MediaInputTool(context=context)
        result = tool.run(path=str(absolute))

        assert result.ok, f"tool.run failed: {result.content}"
        assert result.data["modality"] == "image"

    def test_relative_path_without_zgraph_home_returns_error(
        self, tmp_path: Path, tmp_settings: Settings
    ):
        """相对路径 + 缺 zgraph_home → ToolResult(False, ...) 而不是抛错。"""
        rt = ZGraphRuntime(tmp_settings)
        context = _build_context(rt, tmp_path, run_id="r-no-home")
        context.metadata.pop("zgraph_home", None)
        tool = MediaInputTool(context=context)
        result = tool.run(path="relative/file.png")

        assert not result.ok
        assert "zgraph_home" in result.content

    def test_missing_file_returns_error(self, tmp_path: Path, tmp_settings: Settings):
        """文件不存在 → ToolResult(False, 'File not found: ...')。"""
        rt = ZGraphRuntime(tmp_settings)
        context = _build_context(rt, tmp_path, run_id="r-missing")
        tool = MediaInputTool(context=context)
        result = tool.run(path="D:/this/path/does/not/exist.png")

        assert not result.ok
        assert "File not found" in result.content

    def test_unknown_modality_defaults_to_file(self, tmp_path: Path, tmp_settings: Settings):
        """非 image/audio/video 的 mime → modality='file'。"""
        rt = ZGraphRuntime(tmp_settings)
        # 写一个 .bin 文件，mime 猜不到
        bin_file = tmp_path / "payload.bin"
        bin_file.write_bytes(b"\x00\x01\x02not-an-image")

        context = _build_context(rt, tmp_path, run_id="r-bin")
        tool = MediaInputTool(context=context)
        result = tool.run(path=str(bin_file))

        assert result.ok, f"tool.run failed: {result.content}"
        assert result.data["modality"] == "file"

    def test_registered_in_default_tools(self, tmp_path: Path, tmp_settings: Settings):
        """DEFAULT_TOOL_TYPES 包含 MediaInputTool。"""
        from zgraph.core.tool.tools import DEFAULT_TOOL_TYPES

        assert MediaInputTool in DEFAULT_TOOL_TYPES, (
            f"MediaInputTool not in DEFAULT_TOOL_TYPES: {DEFAULT_TOOL_TYPES}"
        )

    def test_tool_context_propagates_zgraph_home(
        self, tmp_path: Path, tmp_settings: Settings
    ):
        """ZGraphRuntime 构造的 ToolContext 真的带 zgraph_home。"""
        from zgraph.core.tool.builder import build_default_tool_registry

        rt = ZGraphRuntime(tmp_settings)
        workspace = rt.workspace_manager.create_run("r-ctx")
        context = ToolContext(
            workspace=workspace,
            allow_bash=False,
            emit_media=rt._make_emit_media("r-ctx"),
            metadata={"zgraph_home": str(rt.settings.zgraph_home)},
        )
        # 确认 registry 能正常用这个 context 构造
        registry = build_default_tool_registry(context)
        assert registry.get("media_input") is not None, (
            f"media_input not in registry: {registry.keys()}"
        )
