"""Phase 3.4 收尾：验证 runtime 写文件走 storage_dir，不再依赖 outputs_dir 别名。

策略：
- 切断 outputs_dir 别名（monkeypatch 让它指向一个完全不同的目录），验证 runtime
  仍然把文件写到 ``storage_dir``。
- 验证 ``workspace.storage_dir`` 和 ``media_store`` 写到同一棵目录树 —— 这样
  ``emit_media()`` 写入的文件能被 ``runtime.run()`` 的 artifact 扫描捕获。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from zgraph.config import Settings
from zgraph.runtime import ZGraphRuntime
from zgraph.workspace import RunWorkspace, WorkspaceManager


pytestmark = pytest.mark.integration


class TestRuntimeUsesStorageDirNotOutputsAlias:
    def test_offline_execute_writes_to_storage_dir(
        self, tmp_path: Path, tmp_settings: Settings, monkeypatch: pytest.MonkeyPatch
    ):
        """_offline_execute 把 runtime-result.json 写到 storage_dir，而不是 outputs_dir 别名。"""
        rt = ZGraphRuntime(tmp_settings)
        workspace = rt.workspace_manager.create_run("r-offline")
        sentinel = tmp_path / "SENTINEL_SHOULD_NOT_BE_WRITTEN"
        monkeypatch.setattr(
            RunWorkspace, "outputs_dir", property(lambda self: sentinel),
        )

        rt._offline_execute("hi", workspace, {"intent": {"name": "chat"}})

        target = workspace.storage_dir / "runtime-result.json"
        assert target.exists(), f"expected runtime-result.json in {target}"
        payload = json.loads(target.read_text(encoding="utf-8"))
        assert payload["input"] == "hi"
        assert not any(sentinel.glob("*")), (
            "runtime wrote to outputs_dir alias instead of storage_dir"
        )

    def test_run_unprotected_artifacts_come_from_storage_dir(
        self, tmp_settings: Settings, sample_png: bytes
    ):
        """runtime.run() 的 artifacts 列表包含 media_store 写入的 PNG。

        验证 ``workspace.storage_dir`` 和 ``media_store`` 写到同一棵树。
        """
        rt = ZGraphRuntime(tmp_settings)

        image_bytes = sample_png
        workspace = rt.workspace_manager.create_run("r-artifacts")

        # 写一个文件到 media_store
        rt.emit_media(
            run_id="r-artifacts",
            modality="image",
            mime="image/png",
            data=image_bytes,
            name="artifact.png",
        )

        request = {"user_input": "hi", "run_id": "r-artifacts"}
        result_dict = rt._run_unprotected(request, workspace)
        artifacts = result_dict.get("artifacts") or []

        assert any(a.endswith(str(Path("r-artifacts") / "artifact.png")) for a in artifacts), (
            f"artifact.png not in artifacts: {artifacts}"
        )
        # offline 分支还会写 runtime-result.json
        assert any(a.endswith("runtime-result.json") for a in artifacts)

    def test_storage_dir_and_media_store_root_aligned(
        self, tmp_settings: Settings
    ):
        """workspace.storage_dir 和 media_store.root 一致。

        这是 Phase 3.4 的核心修复点：之前两者可能分家（zgraph_home/storage vs
        tmp_store_path），现在统一走 tmp_store_path。
        """
        rt = ZGraphRuntime(tmp_settings)
        workspace = rt.workspace_manager.create_run("r-align")
        assert workspace.storage_dir.parent == rt.media_store.root, (
            f"storage_dir.parent={workspace.storage_dir.parent} "
            f"!= media_store.root={rt.media_store.root}"
        )

    def test_workspace_manager_default_storage_root(self, tmp_path: Path):
        """不传 storage_root 时，WorkspaceManager 走 root/storage（向后兼容）。"""
        wm = WorkspaceManager(tmp_path)
        assert wm.storage_root == tmp_path / "storage"
        ws = wm.create_run("r-default")
        assert ws.storage_dir == tmp_path / "storage" / "r-default"

    def test_workspace_manager_explicit_storage_root(self, tmp_path: Path):
        """显式传 storage_root 时，storage_dir 落到 storage_root 下。"""
        custom = tmp_path / "custom-storage"
        wm = WorkspaceManager(tmp_path, storage_root=custom)
        assert wm.storage_root == custom
        ws = wm.create_run("r-custom")
        assert ws.storage_dir == custom / "r-custom"

    def test_runtime_uses_settings_tmp_store_path(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        """Runtime.__init__ 把 ``settings.tmp_store_path`` 注入到 WorkspaceManager。"""
        custom = tmp_path / "media-store"
        monkeypatch.setenv("ZGRAPH_TMP_STORE_PATH", str(custom))
        rt = ZGraphRuntime(Settings.from_env())
        assert rt.workspace_manager.storage_root == custom
        ws = rt.workspace_manager.create_run("r-rt")
        assert ws.storage_dir == custom / "r-rt"
        # 同时 media_store 也写到 custom
        assert rt.media_store.root == custom
