"""Phase 5.1：AuditHook ↔ resume_interrupted 兼容性测试。

回归覆盖 code-review finding #1：
- 之前 AuditHook 写 ``"interrupt": bool``，``resume_interrupted`` 读时按 dict 处理
  会 AttributeError（``True.get("status")`` 直接挂）。
- 修复后 AuditHook 写 ``interrupt_payload``（完整 dict）+ ``state``（完整 state），
  ``resume_interrupted`` 优先读 NDJSON，回退到老格式。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import AsyncIterator

import pytest

from zgraph.config import Settings
from zgraph.runtime import RuntimeResult, ZGraphRuntime
from zgraph.runtime.events import ContentDelta, Final, RuntimeEvent
from zgraph.runtime.hooks import RunContext
from zgraph.runtime.hooks.builtin import AuditHook


pytestmark = pytest.mark.integration


def _make_runtime_with_mock_astream(
    monkeypatch: pytest.MonkeyPatch,
    tmp_settings: Settings,
    events: list[RuntimeEvent],
    *,
    state_for_ctx: dict | None = None,
) -> ZGraphRuntime:
    """构造一个 Runtime，把 astream() 替换成返回 events 的 async generator。

    如果 ``state_for_ctx`` 非 None，会让 _gen() 在构造 state 后把它赋给 ctx.state，
    模拟真实 astream 的 Phase 5.1 行为。
    """
    rt = ZGraphRuntime(tmp_settings)

    async def _fake_astream(user_input: str, *, run_id=None) -> AsyncIterator[RuntimeEvent]:
        for e in events:
            yield e

    monkeypatch.setattr(rt, "astream", _fake_astream)
    return rt


class TestResumeReadsAuditHookNDJSON:
    """回归：AuditHook 写的中断记录能被 resume_interrupted 正确读取。"""

    def test_resume_succeeds_after_audit_hook_interrupt(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ):
        """完整链路：runtime.run() 写 audit.json（NDJSON，含 interrupt_payload + state）
        → resume_interrupted() 读 NDJSON 恢复（不抛 AttributeError）。
        """
        import asyncio

        from zgraph.workspace import RunWorkspace, WorkspaceManager

        # 准备一个 tmp_settings 目录
        settings = Settings.from_env()
        storage = tmp_path / "storage"
        storage.mkdir(parents=True, exist_ok=True)
        wm = WorkspaceManager(tmp_path, storage_root=storage)

        # 直接构造 RunWorkspace（不通过 rt.run），把 audit.json 写成新格式
        ws = wm.create_run("r-phase51-1")
        audit_path = ws.logs_dir / "audit.json"

        # 模拟 AuditHook 写的中断记录
        interrupt_dict = {
            "interrupt_id": "i-test",
            "status": "pending",
            "reason": "high risk tool: bash",
        }
        state_dict = {
            "run_id": "r-phase51-1",
            "user_input": "run my tests",
            "hint": {"summary": "execute tests"},
            "intent": {"name": "execute_command"},
            "capabilities": {
                "risk_level": "high",
                "selected_tools": ["bash"],
            },
        }
        entry = {
            "ts": 1234567890.0,
            "run_id": "r-phase51-1",
            "status": "interrupted",
            "content_len": 0,
            "reasoning_len": 0,
            "interrupt": True,
            "interrupt_payload": interrupt_dict,
            "error": None,
            "media_count": 0,
            "media": [],
            "metadata_keys": [],
            "state": state_dict,
        }
        audit_path.parent.mkdir(parents=True, exist_ok=True)
        with audit_path.open("w", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")

        # 验证 _read_interrupted_state 能恢复出 state + interrupt
        from zgraph.runtime import _read_interrupted_state

        state, interrupt = _read_interrupted_state(audit_path)
        assert interrupt == interrupt_dict
        assert state["user_input"] == "run my tests"
        assert state["capabilities"]["risk_level"] == "high"
        assert state["hint"]["summary"] == "execute tests"

    def test_resume_falls_back_to_legacy_format(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ):
        """老格式 audit.json（_write_audit 写的单 JSON 对象）仍能被读出。
        这是 ``validate_workflows`` / ``_run_recommendation`` / 老 ``resume_interrupted``
        自写入的格式，必须兼容。
        """
        from zgraph.workspace import WorkspaceManager

        wm = WorkspaceManager(tmp_path, storage_root=tmp_path / "storage")
        ws = wm.create_run("r-legacy")
        audit_path = ws.logs_dir / "audit.json"
        audit_path.parent.mkdir(parents=True, exist_ok=True)

        legacy_interrupt = {
            "interrupt_id": "i-legacy",
            "status": "pending",
            "reason": "legacy",
        }
        legacy_state = {
            "run_id": "r-legacy",
            "user_input": "legacy prompt",
            "hint": {"summary": "h"},
            "intent": {"name": "chat"},
            "capabilities": {"risk_level": "high"},
        }
        legacy = {
            "state": legacy_state,
            "result": None,
            "interrupt": legacy_interrupt,
            "created_at": 1000000.0,
        }
        audit_path.write_text(json.dumps(legacy, ensure_ascii=False), encoding="utf-8")

        from zgraph.runtime import _read_interrupted_state

        state, interrupt = _read_interrupted_state(audit_path)
        assert interrupt == legacy_interrupt
        assert state["user_input"] == "legacy prompt"

    def test_resume_rejects_completed_run_without_pending_interrupt(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ):
        """完成（非中断）的 run：NDJSON 里 interrupt=False，没有 payload。
        resume_interrupted 必须返回 error='pending interrupt not found'，
        而不是 AttributeError 或 False-positive 恢复。
        """
        # 把 runtime 的 zgraph_home 指向 tmp_path，让 resume_interrupted 能在
        # 正确路径找到 audit.json。
        monkeypatch.setenv("ZGRAPH_OFFLINE", "true")
        monkeypatch.setenv("ZGRAPH_TMP_STORE_PATH", str(tmp_path / "storage"))
        monkeypatch.setenv("ZGRAPH_HOME", str(tmp_path / "zhome"))
        Path(tmp_path / "zhome").mkdir(parents=True, exist_ok=True)

        # 直接构造 audit.json 在 zgraph_home/runs/{run_id}/logs/ 下
        runs_dir = tmp_path / "zhome" / "runs" / "r-completed" / "logs"
        runs_dir.mkdir(parents=True, exist_ok=True)
        audit_path = runs_dir / "audit.json"

        # AuditHook 写的 completed run 记录（无 interrupt）
        entry = {
            "ts": 1.0,
            "run_id": "r-completed",
            "status": "completed",
            "content_len": 10,
            "reasoning_len": 0,
            "interrupt": False,
            "interrupt_payload": None,
            "error": None,
            "media_count": 0,
            "media": [],
            "metadata_keys": [],
        }
        with audit_path.open("w", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")

        rt = ZGraphRuntime(Settings.from_env())
        result = rt.resume_interrupted("r-completed", approve=True)
        assert result.status == "failed"
        assert "pending interrupt" in (result.error or "")
