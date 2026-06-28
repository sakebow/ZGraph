"""Phase 4：GuardianHook 测试。"""

from __future__ import annotations

from pathlib import Path

import pytest

from zgraph.config import Settings
from zgraph.runtime import RuntimeResult
from zgraph.runtime.events import ContentDelta, Final, Interrupt, ToolCallStart
from zgraph.runtime.hooks import RunContext
from zgraph.runtime.hooks.guardian_hook import GuardianHook


pytestmark = pytest.mark.integration


def _settings() -> Settings:
    return Settings.from_env()


def _ctx(run_id: str = "r1") -> RunContext:
    return RunContext(
        run_id=run_id,
        user_input="hi",
        settings=_settings(),
        started_at=0.0,
    )


def _runtime_result(**kwargs) -> RuntimeResult:
    """构造 RuntimeResult，默认带完整 capabilities 让 validate 通过。

    接受 ``capabilities=`` 关键字做 merge（不是替换），这样测试可以只覆盖
    自己关心的字段而保留 required_tools / retrieval_strategy 等必要字段。
    """
    defaults = dict(
        run_id="r1",
        status="completed",
        content="ok",
        hint={"summary": "x"},
        intent={"name": "chat"},
        todo=[{"id": 1, "item": "y"}],
        capabilities={
            "selected_tools": [],
            "required_tools": [],
            "risk_level": "low",
            "retrieval_strategy": "word",
        },
    )
    # capabilities 走 merge，其它字段走 replace
    if "capabilities" in kwargs:
        merged_caps = dict(defaults["capabilities"])
        merged_caps.update(kwargs.pop("capabilities"))
        defaults["capabilities"] = merged_caps
    defaults.update(kwargs)
    return RuntimeResult(**defaults)


# ---------------------------------------------------------------------------
# Hook 行为
# ---------------------------------------------------------------------------


class TestGuardianHookPassthrough:
    async def test_non_final_event_passthrough(self):
        hook = GuardianHook()
        ctx = _ctx()
        out = await hook(ContentDelta(text="hi"), ctx)
        assert out.text == "hi"

    async def test_low_risk_final_passthrough(self):
        hook = GuardianHook()
        ctx = _ctx()
        rt = _runtime_result(capabilities={"selected_tools": ["read"], "risk_level": "low"})
        out = await hook(
            Final(run_id="r1", status="completed", finish_reason="stop", runtime_result=rt),
            ctx,
        )
        assert out.status == "completed"
        # risk_level 透传（仍是 low）
        assert out.runtime_result.capabilities["risk_level"] == "low"


class TestGuardianHookInterrupt:
    async def test_high_risk_tool_triggers_interrupt(self):
        hook = GuardianHook()
        ctx = _ctx()
        # 累积 bash tool call
        await hook(ToolCallStart(tool_call_id="t1", tool_name="bash"), ctx)
        rt = _runtime_result(capabilities={"selected_tools": ["bash"], "risk_level": "low"})
        out = await hook(
            Final(run_id="r1", status="completed", finish_reason="stop", runtime_result=rt),
            ctx,
        )
        assert out.status == "interrupted"
        assert out.runtime_result.interrupt is not None
        assert "bash" in out.runtime_result.interrupt["reason"]
        assert out.runtime_result.interrupt_token is not None
        # GuardianHook 把 Interrupt 信息存在 ctx.metadata
        assert "guardian_interrupt" in ctx.metadata

    async def test_validation_failure_triggers_interrupt(self):
        hook = GuardianHook()
        ctx = _ctx()
        rt = _runtime_result(hint={})  # hint 缺失 → validate 失败
        out = await hook(
            Final(run_id="r1", status="completed", finish_reason="stop", runtime_result=rt),
            ctx,
        )
        assert out.status == "interrupted"
        assert "missing hint" in out.runtime_result.interrupt["reason"]

    async def test_medium_risk_does_not_interrupt(self):
        hook = GuardianHook()
        ctx = _ctx()
        # medium risk 不应触发 interrupt（ApproveWorkflow 允许）
        await hook(ToolCallStart(tool_call_id="t1", tool_name="write"), ctx)
        rt = _runtime_result(capabilities={"selected_tools": ["write"], "risk_level": "medium"})
        out = await hook(
            Final(run_id="r1", status="completed", finish_reason="stop", runtime_result=rt),
            ctx,
        )
        assert out.status == "completed"


# ---------------------------------------------------------------------------
# Runtime 默认注册 GuardianHook
# ---------------------------------------------------------------------------


class TestRuntimeGuardianHookIntegration:
    def test_runtime_includes_guardian_hook_by_default(self):
        from zgraph.runtime import ZGraphRuntime

        rt = ZGraphRuntime(_settings())
        names = [type(h).__name__ for h in rt.hooks]
        assert "GuardianHook" in names
        assert "AuditHook" in names
        assert "MetricsHook" in names
        assert "PIIFilterHook" in names
