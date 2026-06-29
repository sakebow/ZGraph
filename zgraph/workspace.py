from __future__ import annotations

import shutil
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


WORKSPACE_SUBDIRS = ("tmp", "drafts", "artifacts", "logs", "scripts")


def ensure_inside_workspace(path: Path, workspace: Path) -> Path:
    """工作空间保障"""
    resolved_path = path.resolve()
    resolved_workspace = workspace.resolve()
    if not resolved_path.is_relative_to(resolved_workspace):
        raise PermissionError("Path escapes current run workspace")
    return resolved_path


@dataclass(slots=True)
class RunWorkspace:

    """运行工作空间。

    目录布局（Phase 3 整合后）：
        runs/{run_id}/logs/      → audit.json / conversation.json
        storage/{run_id}/        → 媒体 + 工作流产物（替代旧的 runs/{run_id}/outputs/）
    """
    root: Path
    run_id: str

    # Phase 3.4 收尾：媒体 + 工作流产物的统一根目录。
    # 默认等于 ``settings.tmp_store_path``；由 ``WorkspaceManager`` 注入，
    # 这里只是承接。把它和 ``root`` 解耦，避免 ``storage_dir`` 写死到
    # ``zgraph_home/storage``（这样 ``ZGRAPH_TMP_STORE_PATH`` 一改，``storage_dir``
    # 就会和 media_store 写到的位置分家）。
    storage_root: Path | None = None

    @property
    def run_dir(self) -> Path:
        """运行目录"""
        return self.root / "runs" / self.run_id

    @property
    def tmp_dir(self) -> Path:
        """tmp目录"""
        return self.run_dir / "tmp"

    @property
    def drafts_dir(self) -> Path:
        """drafts目录"""
        return self.run_dir / "drafts"

    @property
    def artifacts_dir(self) -> Path:
        """artifacts目录"""
        return self.run_dir / "artifacts"

    @property
    def logs_dir(self) -> Path:
        """logs目录"""
        return self.run_dir / "logs"

    @property
    def scripts_dir(self) -> Path:
        """scripts目录"""
        return self.run_dir / "scripts"

    @property
    def storage_dir(self) -> Path:
        """Phase 3：媒体 + 工作流产物的统一目录。

        位置：``{storage_root}/{run_id}/``（默认 ``storage_root = zgraph_home/storage``，
        即 ``tmp_store_path`` 默认值）。这样 ``MediaStorage`` 写文件的位置和
        ``workspace.storage_dir`` 永远一致。

        不再用 ``zgraph_home/storage/{run_id}/`` 的硬编码形式 — 让
        ``ZGRAPH_TMP_STORE_PATH`` 覆盖时也对得上。
        """
        base = self.storage_root if self.storage_root is not None else self.root / "storage"
        return base / self.run_id

    # 向后兼容别名：旧代码引用 outputs_dir 的地方仍然可用，但实际指向 storage_dir
    @property
    def outputs_dir(self) -> Path:
        """[已弃用] 等同 ``storage_dir``。保留以兼容老代码；新代码请用 storage_dir。"""
        return self.storage_dir

    def create(self) -> "RunWorkspace":
        """创建"""
        for name in WORKSPACE_SUBDIRS:
            (self.run_dir / name).mkdir(parents=True, exist_ok=True)
        # Phase 3：同步创建 storage_dir
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        return self

    def resolve_user_path(self, user_path: str | Path, *, base: str = "tmp") -> Path:
        """解析用户路径"""
        raw = Path(user_path)
        if raw.is_absolute():
            candidate = raw
        else:
            candidate = self.run_dir / base / raw
        return ensure_inside_workspace(candidate, self.run_dir)

    def iter_paths(self, subdir: str = "tmp") -> Iterable[Path]:
        """iterpaths"""
        directory = ensure_inside_workspace(self.run_dir / subdir, self.run_dir)
        if not directory.exists():
            return []
        return directory.rglob("*")


class WorkspaceManager:

    """工作空间管理器。"""
    def __init__(self, root: Path, *, storage_root: Path | None = None) -> None:
        """初始化实例属性。

            参数:
                root: 根（Path）。
                storage_root: 媒体 + 工作流产物的统一根目录（默认 ``root/storage``）。
                    传 ``None`` 时回退到 ``root/storage``，与 ``settings.tmp_store_path``
                    默认值一致。
            """
        self.root = root.expanduser()
        self.storage_root = (
            storage_root.expanduser() if storage_root is not None else self.root / "storage"
        )

    def create_run(self, run_id: str | None = None) -> RunWorkspace:
        run_id = run_id or uuid.uuid4().hex
        return RunWorkspace(self.root, run_id, storage_root=self.storage_root).create()

    def cleanup_expired(self, ttl_seconds: int) -> list[Path]:
        runs_dir = self.root / "runs"
        if not runs_dir.exists():
            return []

        now = time.time()
        removed: list[Path] = []
        for run_dir in runs_dir.iterdir():
            if not run_dir.is_dir():
                continue
            try:
                age = now - run_dir.stat().st_mtime
            except OSError:
                continue
            if age < ttl_seconds:
                continue

            audit_dir = run_dir / "logs"
            persistent_dir = run_dir / "artifacts"
            archive_dir = self.root / "audit" / run_dir.name
            archive_dir.mkdir(parents=True, exist_ok=True)

            if audit_dir.exists():
                for log_file in audit_dir.glob("*"):
                    if log_file.is_file():
                        shutil.copy2(log_file, archive_dir / log_file.name)

            if persistent_dir.exists():
                for artifact in persistent_dir.glob("*"):
                    if artifact.is_file() and artifact.name.endswith(".keep"):
                        shutil.copy2(artifact, archive_dir / artifact.name)

            shutil.rmtree(run_dir, ignore_errors=True)
            removed.append(run_dir)
        return removed
