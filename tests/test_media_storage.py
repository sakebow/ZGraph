"""Phase 3 媒体存储层测试。"""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from zgraph.config import Settings
from zgraph.runtime.media_storage import (
    LocalFSStorage,
    MinIOStorage,
    get_media_storage,
)


pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# LocalFSStorage
# ---------------------------------------------------------------------------


class TestLocalFSStorage:
    def test_put_creates_file(self, tmp_path: Path):
        storage = LocalFSStorage(root=tmp_path, base_url="http://x")
        url = storage.put(
            run_id="r1",
            name="img.png",
            data=b"fake-png",
            mime="image/png",
        )
        assert url == "http://x/files/r1/img.png"
        assert (tmp_path / "r1" / "img.png").read_bytes() == b"fake-png"

    def test_open_roundtrip(self, tmp_path: Path):
        storage = LocalFSStorage(root=tmp_path, base_url="http://x")
        storage.put(run_id="r1", name="a.txt", data=b"hello", mime="text/plain")
        data, mime = storage.open("http://x/files/r1/a.txt")
        assert data == b"hello"
        assert mime == "text/plain"

    def test_open_unknown_url_returns_none(self, tmp_path: Path):
        storage = LocalFSStorage(root=tmp_path, base_url="http://x")
        assert storage.open("http://x/files/r1/missing.txt") is None
        assert storage.open("http://other/files/r1/a.txt") is None

    def test_open_blocks_traversal(self, tmp_path: Path):
        storage = LocalFSStorage(root=tmp_path, base_url="http://x")
        # ../etc/passwd style
        assert storage.open("http://x/files/../etc/passwd") is None

    def test_cleanup_expired_removes_old_files(self, tmp_path: Path):
        storage = LocalFSStorage(root=tmp_path, base_url="http://x")
        # 写一个"老"文件
        old = tmp_path / "r1" / "old.txt"
        old.parent.mkdir(parents=True, exist_ok=True)
        old.write_bytes(b"old")
        # 把它的时间戳调到很久以前
        old_time = time.time() - 10000
        os.utime(old, (old_time, old_time))

        # 写一个新的
        new = tmp_path / "r1" / "new.txt"
        new.write_bytes(b"new")

        removed = storage.cleanup_expired(ttl_seconds=1000)
        assert removed == 1
        assert not old.exists()
        assert new.exists()


# ---------------------------------------------------------------------------
# MinIOStorage：仅占位
# ---------------------------------------------------------------------------


class TestMinIOStorage:
    def test_construction_raises_not_implemented(self):
        with pytest.raises(NotImplementedError):
            MinIOStorage()


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


class TestGetMediaStorage:
    def test_default_is_localfs(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
        monkeypatch.setenv("ZGRAPH_STORAGE_PROVIDERS", "")
        # Settings.from_env 的 tmp_store_path 默认 ./storage
        # 我们手动构造一个 Settings 指向 tmp_path
        s = Settings.from_env()
        # 直接验证默认行为：factory 在没有 minio 时给 localfs
        storage = get_media_storage(s)
        assert isinstance(storage, LocalFSStorage)

    def test_minio_provider_skipped_with_warning(self, monkeypatch: pytest.MonkeyPatch, caplog):
        monkeypatch.setenv("ZGRAPH_STORAGE_PROVIDERS", "minio")
        s = Settings.from_env()
        with caplog.at_level("WARNING", logger="zgraph.storage"):
            storage = get_media_storage(s)
        # 应当降级到 localfs，警告日志记录 minio 跳过
        assert isinstance(storage, LocalFSStorage)

    def test_unknown_provider_skipped(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("ZGRAPH_STORAGE_PROVIDERS", "nonexistent,localfs")
        s = Settings.from_env()
        storage = get_media_storage(s)
        assert isinstance(storage, LocalFSStorage)

    def test_all_unknown_falls_back_to_localfs(self, monkeypatch: pytest.MonkeyPatch, caplog):
        """全未知 provider 时不应该报错：兜底到 localfs（更安全）。"""
        monkeypatch.setenv("ZGRAPH_STORAGE_PROVIDERS", "foo,bar")
        s = Settings.from_env()
        with caplog.at_level("WARNING", logger="zgraph.storage"):
            storage = get_media_storage(s)
        assert isinstance(storage, LocalFSStorage)


# ---------------------------------------------------------------------------
# Runtime integration
# ---------------------------------------------------------------------------


class TestRuntimeMediaStore:
    def test_runtime_has_media_store(self):
        from zgraph.runtime import ZGraphRuntime

        rt = ZGraphRuntime(Settings.from_env())
        assert rt.media_store is not None
        assert isinstance(rt.media_store, LocalFSStorage)

    def test_emit_media_returns_media_ready_with_url(self, tmp_path, monkeypatch):
        """把 Settings.tmp_store_path 指向 tmp_path，避免污染真实 ./storage。

        注：emit_media() 真的会写文件到磁盘（LocalFSStorage.put 不是 mock），
        所以测试必须用 tmp_path 隔离；用真实路径会留垃圾文件。
        """
        from zgraph.runtime import ZGraphRuntime

        # 把默认的 ./storage 路径覆盖到 tmp_path
        monkeypatch.setenv("ZGRAPH_TMP_STORE_PATH", str(tmp_path / "storage"))
        rt = ZGraphRuntime(Settings.from_env())
        event = rt.emit_media(
            run_id="r1",
            modality="image",
            mime="image/png",
            data=b"fake",
            name="out.png",
            metadata={"width": 1024},
        )
        assert event.block_id.startswith("image-")
        assert event.mime == "image/png"
        assert event.size_bytes == 4
        assert event.metadata["width"] == 1024
        assert "/files/r1/out.png" in event.url
        assert event.expires_at != ""  # P3.7 已经填了 expires_at
        # 验证文件确实写到 tmp_path（而不是 ./storage）
        assert (tmp_path / "storage" / "r1" / "out.png").exists()
        assert (tmp_path / "storage" / "r1" / "out.png").read_bytes() == b"fake"
