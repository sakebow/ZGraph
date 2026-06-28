"""媒体存储抽象（Phase 3）。

设计：
- MediaStorage Protocol：统一的 put / open / cleanup_expired 接口
- LocalFSStorage：默认实现，文件存到 ``{root}/{run_id}/{name}``
- MinIOStorage / S3Storage：本次仅留 Protocol 占位，NotImplementedError
- Factory ``get_media_storage(settings)`` 按 ZGRAPH_STORAGE_PROVIDERS 列表顺序选第一个健康后端
"""

from __future__ import annotations

import logging
import mimetypes
import os
import time
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from zgraph.config import Settings

logger = logging.getLogger("zgraph.storage")


@runtime_checkable
class MediaStorage(Protocol):
    """媒体存储统一接口。

    所有后端（LocalFS / MinIO / S3）都实现这三个方法。
    """

    def put(
        self,
        *,
        run_id: str,
        name: str,
        data: bytes,
        mime: str,
    ) -> str:
        """存文件，返回可访问的 URL。

        参数:
            run_id: 本次运行的唯一标识符（str）。
            name: 文件名（不含路径）（str）。
            data: 字节内容（bytes）。
            mime: MIME 类型（str）。

        返回:
            可访问的 URL（str）。
        """
        ...

    def open(self, url: str) -> tuple[bytes, str] | None:
        """按 URL 读出 (bytes, mime)。找不到返回 None。

        参数:
            url: ``put`` 返回的 URL（str）。

        返回:
            (bytes, mime) 二元组；找不到返回 None。
        """
        ...

    def cleanup_expired(self, ttl_seconds: int) -> int:
        """清理早于 ttl 的过期文件，返回删除条数。"""
        ...


class LocalFSStorage:
    """本地文件系统实现（默认后端）。

    文件位置：``{root}/{run_id}/{name}``
    URL 形式：``{base_url}/files/{run_id}/{name}``
    """

    def __init__(self, *, root: Path, base_url: str) -> None:
        self.root = Path(root)
        self.base_url = base_url.rstrip("/")

    def put(
        self,
        *,
        run_id: str,
        name: str,
        data: bytes,
        mime: str,
    ) -> str:
        run_dir = self.root / run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        path = run_dir / name
        path.write_bytes(data)
        return f"{self.base_url}/files/{run_id}/{name}"

    def open(self, url: str) -> tuple[bytes, str] | None:
        prefix = f"{self.base_url}/files/"
        if not url.startswith(prefix):
            return None
        rel = url[len(prefix):]
        # 安全检查：禁止 ../
        if ".." in rel.split("/"):
            return None
        path = self.root / rel
        if not path.is_file():
            return None
        mime, _ = mimetypes.guess_type(str(path))
        return path.read_bytes(), mime or "application/octet-stream"

    def cleanup_expired(self, ttl_seconds: int) -> int:
        cutoff = time.time() - ttl_seconds
        count = 0
        for p in self.root.glob("*/"):
            if not p.is_dir():
                continue
            for child in p.glob("*"):
                try:
                    if child.is_file() and child.stat().st_mtime < cutoff:
                        child.unlink()
                        count += 1
                except FileNotFoundError:
                    continue
        return count


class MinIOStorage:
    """MinIO（S3 兼容）实现占位。

    本次 Phase 3 不实现，仅保留 Protocol 槽位和 NotImplementedError，
    留给未来扩展。Factory 见到 ``minio`` provider 也会跳过。
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        raise NotImplementedError(
            "MinIOStorage is not implemented in Phase 3. "
            "Use LocalFSStorage (default) for now."
        )

    def put(self, **kwargs: Any) -> str:
        raise NotImplementedError

    def open(self, url: str) -> tuple[bytes, str] | None:
        raise NotImplementedError

    def cleanup_expired(self, ttl_seconds: int) -> int:
        raise NotImplementedError


def _resolve_media_root(settings: Settings) -> Path:
    """从 settings 读 media 根目录。

    优先 ``settings.tmp_store_path``；缺省回退到 ``{zgraph_home}/storage``。
    """
    root = getattr(settings, "tmp_store_path", None)
    if root is None:
        root = settings.zgraph_home / "storage"
    return Path(root)


def _resolve_media_base_url(settings: Settings) -> str:
    """媒体 URL 的 base 前缀。

    默认 ``http://{host}:{port}``。覆盖用 env ``ZGRAPH_MEDIA_BASE_URL``。
    """
    explicit = os.environ.get("ZGRAPH_MEDIA_BASE_URL", "").strip()
    if explicit:
        return explicit.rstrip("/")
    return f"http://{settings.host}:{settings.port}"


def get_media_storage(settings: Settings) -> MediaStorage:
    """工厂：按 ``ZGRAPH_STORAGE_PROVIDERS`` 列表顺序选第一个可初始化的后端。

    默认 ``["localfs"]``。MinIO 等占位后端目前会被跳过（构造时抛 NotImplementedError），
    留下次实现。如果列表里所有候选都跳过，最终兜底用 LocalFSStorage。

    参数:
        settings: 全局配置（Settings）。

    返回:
        第一个能构造成功的 MediaStorage 实例。
    """
    raw = os.environ.get("ZGRAPH_STORAGE_PROVIDERS", "localfs").strip()
    if not raw:
        raw = "localfs"
    candidates = [n.strip().lower() for n in raw.split(",") if n.strip()]

    root = _resolve_media_root(settings)
    base_url = _resolve_media_base_url(settings)

    # 优先按用户声明顺序尝试；声明的都不能用就兜底 localfs
    for name in candidates:
        if name == "localfs":
            try:
                root.mkdir(parents=True, exist_ok=True)
                return LocalFSStorage(root=root, base_url=base_url)
            except Exception as exc:
                logger.warning("LocalFSStorage init failed: %s", exc)
                continue
        elif name in ("minio", "s3"):
            logger.warning(
                "MediaStorage provider %r is not implemented yet; skipping. "
                "LocalFSStorage will be used.",
                name,
            )
            continue
        else:
            logger.warning("Unknown media storage provider %r; skipping", name)
            continue

    # 所有声明的 provider 都跳过：兜底 localfs
    logger.warning(
        "No usable media storage backend in ZGRAPH_STORAGE_PROVIDERS=%r; "
        "falling back to LocalFSStorage",
        raw,
    )
    root.mkdir(parents=True, exist_ok=True)
    return LocalFSStorage(root=root, base_url=base_url)
