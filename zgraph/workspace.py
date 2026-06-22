from __future__ import annotations

import shutil
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


WORKSPACE_SUBDIRS = ("tmp", "drafts", "artifacts", "logs", "scripts", "outputs")


def ensure_inside_workspace(path: Path, workspace: Path) -> Path:
    """工作空间保障"""
    resolved_path = path.resolve()
    resolved_workspace = workspace.resolve()
    if not resolved_path.is_relative_to(resolved_workspace):
        raise PermissionError("Path escapes current run workspace")
    return resolved_path


@dataclass(slots=True)
class RunWorkspace:

    """运行工作空间。"""
    root: Path
    run_id: str

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
    def outputs_dir(self) -> Path:
        """outputs目录"""
        return self.run_dir / "outputs"

    def create(self) -> "RunWorkspace":
        """创建"""
        for name in WORKSPACE_SUBDIRS:
            (self.run_dir / name).mkdir(parents=True, exist_ok=True)
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
    def __init__(self, root: Path) -> None:
        """初始化实例属性。
        
            参数:
                root: 根（Path）
            """
        self.root = root.expanduser()

    def create_run(self, run_id: str | None = None) -> RunWorkspace:
        run_id = run_id or uuid.uuid4().hex
        return RunWorkspace(self.root, run_id).create()

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
