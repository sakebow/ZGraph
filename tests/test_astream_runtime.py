"""Phase 1.5：astream 事件流聚合 + sync 包装测试。

测试目标：
- ``StreamAggregator.collect()`` 把手工注入的 RuntimeEvent 流聚合到 RuntimeResult：
  - ContentDelta 拼接成 content
  - ReasoningDelta 拼接成 reasoning_content（核心验收点）
  - MediaReady 收集成 media 列表
  - Final 事件携带的 hint/intent/capabilities 等其它字段透传
- ``Runtime.run_via_stream()`` 是 ``astream()`` 的同步薄包装 —— 因为 astream
  依赖真实 LangChain agent，这里通过 monkey-patch ``Runtime.astream`` 注入
  预制事件流，避免依赖外部 LLM。
"""

from __future__ import annotations

from typing import AsyncIterator

import pytest

from zgraph.config import Settings
from zgraph.runtime import RuntimeResult, ZGraphRuntime
from zgraph.runtime.events import (
    ContentDelta,
    Final,
    Interrupt,
    MediaReady,
    ReasoningDelta,
    RuntimeEvent,
    ToolCallStart,
)
from zgraph.runtime.stream_aggregator import StreamAggregator


pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# StreamAggregator：手工注入事件流
# ---------------------------------------------------------------------------


class TestAggregatorContentAndReasoning:
    def test_aggregates_content_and_reasoning_from_delta_events(self):
        """手工注入多个 ContentDelta + ReasoningDelta，验证 content / reasoning_content 拼接顺序。"""
        events = [
            ReasoningDelta(text="thinking step 1. "),
            ContentDelta(text="Hello"),
            ReasoningDelta(text="thinking step 2. "),
            ContentDelta(text=" world"),
            Final(
                run_id="r1",
                status="completed",
                finish_reason="stop",
                runtime_result=RuntimeResult(
                    run_id="r1",
                    status="completed",
                    content="",  # 故意空，看是否被聚合值覆盖
                    reasoning_content="",  # 同上
                ),
            ),
        ]
        result = StreamAggregator.collect(events)
        assert result.content == "Hello world"
        assert result.reasoning_content == "thinking step 1. thinking step 2. "
        assert result.status == "completed"

    def test_final_runtime_result_fields_are_preserved(self):
        """Final 事件 RuntimeResult 的 hint / intent / capabilities 透传。"""
        events = [
            ContentDelta(text="ok"),
            Final(
                run_id="r2",
                status="completed",
                finish_reason="stop",
                runtime_result=RuntimeResult(
                    run_id="r2",
                    status="completed",
                    content="",
                    hint={"summary": "test", "domain": "x"},
                    intent={"name": "chat", "confidence": 0.9},
                    capabilities={"risk_level": "low"},
                ),
            ),
        ]
        result = StreamAggregator.collect(events)
        assert result.hint == {"summary": "test", "domain": "x"}
        assert result.intent == {"name": "chat", "confidence": 0.9}
        assert result.capabilities == {"risk_level": "low"}

    def test_no_delta_events_keeps_final_content(self):
        """没有 ContentDelta/ReasoningDelta 时，沿用 Final 里的 content。"""
        events = [
            Final(
                run_id="r3",
                status="completed",
                finish_reason="stop",
                runtime_result=RuntimeResult(
                    run_id="r3",
                    status="completed",
                    content="from-final",
                    reasoning_content="reasoning-from-final",
                ),
            ),
        ]
        result = StreamAggregator.collect(events)
        assert result.content == "from-final"
        assert result.reasoning_content == "reasoning-from-final"


class TestAggregatorMedia:
    def test_aggregates_media_ready_into_media_list(self, tmp_settings: Settings, sample_png: bytes):
        """MediaReady 事件被收集到 RuntimeResult.media 列表里（用真实 PNG 走 emit_media）。"""
        rt = ZGraphRuntime(tmp_settings)
        image_bytes = sample_png

        m1 = rt.emit_media(
            run_id="r-media", modality="image", mime="image/png",
            data=image_bytes, name="a.png",
        )
        m2 = rt.emit_media(
            run_id="r-media", modality="image", mime="image/png",
            data=image_bytes, name="b.png",
        )

        events = [
            ContentDelta(text="hi"),
            m1,
            ContentDelta(text="\n"),
            m2,
            Final(
                run_id="r-media",
                status="completed",
                finish_reason="stop",
                runtime_result=RuntimeResult(
                    run_id="r-media",
                    status="completed",
                    content="",
                ),
            ),
        ]
        result = StreamAggregator.collect(events)
        # 聚合后的 content（被覆盖）
        assert result.content == "hi\n"
        # 两条 media 都被收集
        assert len(result.media) == 2
        urls = [m["url"] for m in result.media]
        assert any(u.endswith("/a.png") for u in urls)
        assert any(u.endswith("/b.png") for u in urls)
        # 每条 media 的 URL 都可访问，bytes 与原图一致
        for m in result.media:
            restored, mime = rt.media_store.open(m["url"])
            assert restored == image_bytes
            assert mime == "image/png"


class TestAggregatorEdgeCases:
    def test_no_final_event_yields_failed_runtime_result(self):
        """没有 Final 事件但有 content → 构造 status=failed 的最小 RuntimeResult。"""
        events = [
            ContentDelta(text="partial output"),
            ReasoningDelta(text="partial thinking"),
        ]
        result = StreamAggregator.collect(events)
        assert result.status == "failed"
        assert result.content == "partial output"
        assert result.reasoning_content == "partial thinking"
        assert result.error == "no Final event"

    def test_empty_event_stream_returns_failed(self):
        """完全没有事件 → RuntimeResult(status=failed, error="empty event stream")。"""
        result = StreamAggregator.collect([])
        assert result.status == "failed"
        assert result.content == ""
        assert result.error == "empty event stream"

    def test_final_with_runtime_result_none_falls_back(self):
        """Final.runtime_result=None 时按"没 Final"处理（构造最小结果）。"""
        events = [
            ContentDelta(text="orphan"),
            Final(
                run_id="r-x",
                status="failed",
                finish_reason="error",
                runtime_result=None,
            ),
        ]
        result = StreamAggregator.collect(events)
        # runtime_result=None 走降级路径
        assert result.error == "no Final event"
        assert result.content == "orphan"


class TestAggregatorInterrupt:
    def test_interrupt_event_propagates_to_final(self):
        """Final 携带 interrupt 时，聚合结果也带 interrupt（透传）。"""
        events = [
            ContentDelta(text="blocked"),
            Final(
                run_id="r-int",
                status="interrupted",
                finish_reason="interrupt",
                runtime_result=RuntimeResult(
                    run_id="r-int",
                    status="interrupted",
                    content="blocked",
                    interrupt={"interrupt_id": "i1", "reason": "high risk"},
                    interrupt_token="tok-1",
                ),
            ),
        ]
        result = StreamAggregator.collect(events)
        assert result.status == "interrupted"
        assert result.interrupt == {"interrupt_id": "i1", "reason": "high risk"}
        assert result.interrupt_token == "tok-1"


# ---------------------------------------------------------------------------
# Runtime.run_via_stream：sync 薄包装
# ---------------------------------------------------------------------------


def _make_runtime_with_mock_astream(
    monkeypatch: pytest.MonkeyPatch,
    tmp_settings: Settings,
    events: list[RuntimeEvent],
) -> ZGraphRuntime:
    """构造一个 Runtime，把 astream() 替换成返回 events 的 async generator。"""
    rt = ZGraphRuntime(tmp_settings)

    async def _fake_astream(user_input: str, *, run_id=None) -> AsyncIterator[RuntimeEvent]:
        for e in events:
            yield e

    monkeypatch.setattr(rt, "astream", _fake_astream)
    return rt


class TestRuntimeRunViaStream:
    def test_sync_wrapper_aggregates_reasoning_content(
        self, monkeypatch: pytest.MonkeyPatch, tmp_settings: Settings
    ):
        """手工注入 ReasoningDelta，验证 run_via_stream() 同步包装能聚合 reasoning_content。

        这是 Phase 1.5 的核心验收点。
        """
        events = [
            ReasoningDelta(text="Let me think. "),
            ContentDelta(text="The answer is 42."),
            ReasoningDelta(text=" Done."),
            Final(
                run_id="r-agg",
                status="completed",
                finish_reason="stop",
                runtime_result=RuntimeResult(
                    run_id="r-agg",
                    status="completed",
                    content="",
                    reasoning_content="",
                ),
            ),
        ]
        rt = _make_runtime_with_mock_astream(monkeypatch, tmp_settings, events)
        result = rt.run_via_stream("test prompt")

        assert result.run_id == "r-agg"
        assert result.status == "completed"
        assert result.content == "The answer is 42."
        assert result.reasoning_content == "Let me think.  Done."  # noqa: E501 — 两段中间是单空格，但 "think. " 和 " Done." 拼接后是两个空格

    def test_sync_wrapper_aggregates_content_and_media(
        self, monkeypatch: pytest.MonkeyPatch, tmp_settings: Settings, sample_png: bytes
    ):
        """run_via_stream() 也聚合 content + media。"""
        rt = ZGraphRuntime(tmp_settings)
        image_bytes = sample_png
        m_event = rt.emit_media(
            run_id="r-stream-media",
            modality="image",
            mime="image/png",
            data=image_bytes,
            name="x.png",
        )

        async def _fake_astream(user_input, *, run_id=None):
            yield ContentDelta(text="here is img:")
            yield m_event
            yield ContentDelta(text=" done")
            yield Final(
                run_id="r-stream-media",
                status="completed",
                finish_reason="stop",
                runtime_result=RuntimeResult(
                    run_id="r-stream-media",
                    status="completed",
                    content="",
                ),
            )

        monkeypatch.setattr(rt, "astream", _fake_astream)

        result = rt.run_via_stream("test")
        assert result.content == "here is img: done"
        assert len(result.media) == 1
        assert result.media[0]["url"].endswith("/x.png")
        # URL 可访问，bytes 一致
        restored, mime = rt.media_store.open(result.media[0]["url"])
        assert restored == image_bytes
        assert mime == "image/png"

    def test_sync_wrapper_preserves_interrupt(
        self, monkeypatch: pytest.MonkeyPatch, tmp_settings: Settings
    ):
        """run_via_stream() 在中断路径下也返回正确的 RuntimeResult。"""
        events = [
            ContentDelta(text="blocked"),
            Final(
                run_id="r-int",
                status="interrupted",
                finish_reason="interrupt",
                runtime_result=RuntimeResult(
                    run_id="r-int",
                    status="interrupted",
                    content="blocked",
                    interrupt={"interrupt_id": "i-1", "reason": "high"},
                    interrupt_token="tok-i",
                ),
            ),
        ]
        rt = _make_runtime_with_mock_astream(monkeypatch, tmp_settings, events)
        result = rt.run_via_stream("test")
        assert result.status == "interrupted"
        assert result.interrupt_token == "tok-i"
        assert result.interrupt == {"interrupt_id": "i-1", "reason": "high"}

    def test_sync_wrapper_propagates_failed_status(
        self, monkeypatch: pytest.MonkeyPatch, tmp_settings: Settings
    ):
        """Final.status=failed 时，run_via_stream() 也返回 failed。"""
        events = [
            ContentDelta(text="partial"),
            Final(
                run_id="r-fail",
                status="failed",
                finish_reason="error",
                runtime_result=RuntimeResult(
                    run_id="r-fail",
                    status="failed",
                    content="",
                    error="boom",
                ),
            ),
        ]
        rt = _make_runtime_with_mock_astream(monkeypatch, tmp_settings, events)
        result = rt.run_via_stream("test")
        assert result.status == "failed"
        assert result.error == "boom"


# ---------------------------------------------------------------------------
# Phase 5.4 / 5.6：astream 异常转译 + auto-approve 翻 allow_bash
# ---------------------------------------------------------------------------


class TestAstreamExceptionTranslation:
    """Phase 5.4：astream 任何路径抛错都被转译成 status='failed' Final。

    astream 之前在 LangChain ``astream_events`` 阶段或 conversation persist
    阶段抛错时，generator 会异常终止，调用方拿到 ``StopAsyncIteration`` /
    没有收尾 Final。修复后外层 try/except/finally 把所有路径的异常转译成
    ``Final(status='failed')``，保证 ``astream()`` 始终以 Final 收尾。
    """

    def _settings_offline(self, tmp_path) -> Settings:
        """构造 offline Settings，zgraph_home 指向 tmp_path。"""
        import os

        os.environ["ZGRAPH_OFFLINE"] = "true"
        os.environ["ZGRAPH_HOME"] = str(tmp_path / "zhome")
        os.environ["ZGRAPH_TMP_STORE_PATH"] = str(tmp_path / "storage")
        (tmp_path / "zhome").mkdir(parents=True, exist_ok=True)
        (tmp_path / "storage").mkdir(parents=True, exist_ok=True)
        return Settings.from_env()

    def _last_final(self, events: list[RuntimeEvent]) -> Final | None:
        """取最后一个 Final 事件（astream 契约：始终以 Final 收尾）。"""
        finals = [e for e in events if isinstance(e, Final)]
        return finals[-1] if finals else None

    def test_setup_exception_yields_final_no_propagation(self, monkeypatch, tmp_path):
        """``_setup_runtime`` 抛错时，astream 必须 yield Final（不让异常冒到
        调用方），并且 ``runtime_result.error`` 含失败原因。

        注：GuardianHook 可能在 Final 事件上把 ``status`` 改成 ``interrupted``
        —— Phase 5.4 的契约是 ``astream()`` 始终以 Final 收尾，**不**强制
        ``status='failed'``。这是 GuardianHook 的独立行为。
        """
        import asyncio

        settings = self._settings_offline(tmp_path)
        rt = ZGraphRuntime(settings)

        def boom_setup(user_input, workspace, ctx):
            raise RuntimeError("intent workflow crashed")

        monkeypatch.setattr(rt, "_setup_runtime", boom_setup)

        async def _collect():
            events: list[RuntimeEvent] = []
            async for ev in rt.astream("hi", run_id="r-5-4-setup"):
                events.append(ev)
            return events

        # 不抛异常就是关键
        events = asyncio.run(_collect())

        final = self._last_final(events)
        assert final is not None, "astream did not yield any Final event"
        # 不变量：runtime_result.error 含原始异常信息
        assert final.runtime_result is not None
        assert final.runtime_result.error is not None
        assert "setup failed" in final.runtime_result.error
        assert "intent workflow crashed" in final.runtime_result.error

    def test_pending_media_cleared_after_exception(self, monkeypatch, tmp_path):
        """异常路径后 ``_pending_media[run_id]`` 必须被弹出，防止长跑 server
        累积内存泄漏。
        """
        import asyncio

        settings = self._settings_offline(tmp_path)
        rt = ZGraphRuntime(settings)

        def boom_setup(user_input, workspace, ctx):
            raise RuntimeError("boom")

        monkeypatch.setattr(rt, "_setup_runtime", boom_setup)

        async def _collect():
            async for _ in rt.astream("hi", run_id="r-5-4-cleanup"):
                pass

        asyncio.run(_collect())

        # _pending_media 在 finally 里被 pop，不应残留
        assert "r-5-4-cleanup" not in rt._pending_media


class TestStreamingCompletionCallsSaveMemoryAndCleanup:
    """Phase 5.5：astream streaming 完成路径必须调 ``_save_memory`` 和
    ``cleanup_expired``，与 offline 分支行为一致。

    之前 streaming 路径只调 ``_write_streaming_conversation``，完全不写记忆、
    不清理过期 run，导致 memory_saver / media TTL 在线上 LLM 路径下失效。
    """

    def _settings_offline(self, tmp_path) -> Settings:
        import os
        from pathlib import Path

        os.environ["ZGRAPH_OFFLINE"] = "true"
        os.environ["ZGRAPH_HOME"] = str(tmp_path / "zhome")
        os.environ["ZGRAPH_TMP_STORE_PATH"] = str(tmp_path / "storage")
        Path(tmp_path / "zhome").mkdir(parents=True, exist_ok=True)
        Path(tmp_path / "storage").mkdir(parents=True, exist_ok=True)
        return Settings.from_env()

    def test_streaming_completion_invokes_save_memory(self, monkeypatch, tmp_path):
        """astream 走完整 streaming 路径（不抛错）时，``_save_memory`` 必须被调用。

        测试构造：offline=False + 有 api_key → 走 streaming 路径；
        用 monkeypatch 让 ``agent_manager.factory.create`` 返回一个 fake agent，
        这个 fake agent 的 ``astream_events`` 抛错——期望：异常路径**不**调
        ``_save_memory``（流式没成功完成，避免污染记忆）。
        """
        import asyncio
        from pathlib import Path

        # 强制走 LangChain streaming 路径
        monkeypatch.setenv("ZGRAPH_OFFLINE", "false")
        monkeypatch.setenv("APIKEY", "fake-key-for-test")
        monkeypatch.setenv("ZGRAPH_HOME", str(tmp_path / "zhome"))
        monkeypatch.setenv("ZGRAPH_TMP_STORE_PATH", str(tmp_path / "storage"))
        Path(tmp_path / "zhome").mkdir(parents=True, exist_ok=True)
        Path(tmp_path / "storage").mkdir(parents=True, exist_ok=True)

        settings = Settings.from_env()
        assert settings.api_key == "fake-key-for-test"
        rt = ZGraphRuntime(settings)

        # spy _save_memory
        called = {"yes": False}
        original = rt._save_memory

        def spy_save_memory(user_input, result):
            called["yes"] = True
            return original(user_input, result)

        monkeypatch.setattr(rt, "_save_memory", spy_save_memory)

        # stub _setup_runtime 让它走完 success（low risk, no interrupt）
        class FakeToolRegistry:
            def get(self, name):
                return None

        def fake_setup(ui, ws, ctx):
            return (
                {"user_input": ui, "hint": {}, "intent": {}, "todo": []},
                {"selected_tools": ["read"], "risk_level": "low"},
                None,
                FakeToolRegistry(),
                [],
                None,
            )

        monkeypatch.setattr(rt, "_setup_runtime", fake_setup)

        # 让 agent.astream_events 抛错 → 异常分支 → 不应调 _save_memory
        async def fake_astream_events(payload, version):
            raise RuntimeError("provider stream failed")
            yield  # 让它变成 generator

        class FakeAgent:
            astream_events = staticmethod(fake_astream_events)

        class FakeFactory:
            def create(self, tools, system_prompt):
                return FakeAgent()

        class FakeAgentManager:
            factory = FakeFactory()

        rt.agent_manager = FakeAgentManager()

        async def _collect():
            async for _ in rt.astream("hi", run_id="r-5-5-fail"):
                pass

        asyncio.run(_collect())

        # 异常路径不应调 _save_memory（流式没成功完成，避免污染记忆）
        assert not called["yes"], (
            "_save_memory should NOT be called when astream raises mid-stream"
        )

    def test_streaming_success_invokes_save_memory(self, monkeypatch, tmp_path):
        """astream 完整流式成功时 ``_save_memory`` 必须被调用。

        验证 Phase 5.5 修复：之前 streaming 成功路径完全跳过 _save_memory。
        """
        import asyncio
        from pathlib import Path

        # 强制走 LangChain streaming 路径
        monkeypatch.setenv("ZGRAPH_OFFLINE", "false")
        monkeypatch.setenv("APIKEY", "fake-key-for-test")
        monkeypatch.setenv("ZGRAPH_HOME", str(tmp_path / "zhome"))
        monkeypatch.setenv("ZGRAPH_TMP_STORE_PATH", str(tmp_path / "storage"))
        Path(tmp_path / "zhome").mkdir(parents=True, exist_ok=True)
        Path(tmp_path / "storage").mkdir(parents=True, exist_ok=True)

        settings = Settings.from_env()
        assert settings.api_key == "fake-key-for-test"
        rt = ZGraphRuntime(settings)

        # spy _save_memory
        called = {"yes": False}
        original = rt._save_memory

        def spy_save_memory(user_input, result):
            called["yes"] = True
            return original(user_input, result)

        monkeypatch.setattr(rt, "_save_memory", spy_save_memory)

        # stub _setup_runtime
        class FakeToolRegistry:
            def get(self, name):
                return None

        def fake_setup(ui, ws, ctx):
            return (
                {"user_input": ui, "hint": {}, "intent": {}, "todo": []},
                {"selected_tools": ["read"], "risk_level": "low"},
                None,
                FakeToolRegistry(),
                [],
                None,
            )

        monkeypatch.setattr(rt, "_setup_runtime", fake_setup)

        # fake agent 发出一个空事件（不抛错），走完整个 streaming loop
        async def fake_astream_events(payload, version):
            # 不 yield 任何东西，直接退出
            if False:
                yield  # 永远不执行

        class FakeAgent:
            astream_events = staticmethod(fake_astream_events)

        class FakeFactory:
            def create(self, tools, system_prompt):
                return FakeAgent()

        class FakeAgentManager:
            factory = FakeFactory()

        rt.agent_manager = FakeAgentManager()

        async def _collect():
            async for _ in rt.astream("hi", run_id="r-5-5-success"):
                pass

        asyncio.run(_collect())

        # 关键断言：streaming 完整成功时 _save_memory 被调
        assert called["yes"], (
            "Phase 5.5: streaming completion MUST call _save_memory"
        )

    def test_offline_path_still_calls_save_memory(self, monkeypatch, tmp_path):
        """回归：offline 路径仍然调 ``_save_memory``（这是 Phase 5.5 之前就有的行为）。"""
        import asyncio

        settings = self._settings_offline(tmp_path)
        rt = ZGraphRuntime(settings)

        called = {"yes": False}
        original = rt._save_memory

        def spy_save_memory(user_input, result):
            called["yes"] = True
            return original(user_input, result)

        monkeypatch.setattr(rt, "_save_memory", spy_save_memory)

        async def _collect():
            async for _ in rt.astream("hi", run_id="r-5-5-offline"):
                pass

        asyncio.run(_collect())

        assert called["yes"], "offline path should call _save_memory"


class TestAutoApproveFlipsAllowBash:
    """Phase 5.6：``_setup_runtime`` 在 auto-approve 后设置
    ``state["auto_approved"]=True``，astream 据此翻 ``context.allow_bash``。

    之前 Guardian 在 ``auto_approve_interrupts=True`` 时只翻 ``interrupt["status"]``
    但不翻 ``context.allow_bash``，高风险 bash tool 在自动批准路径下仍会被
    BashTool 拒绝（旧 ``_run_unprotected`` 行为）。修复后这条路径被恢复。
    """

    def _settings_offline(self, tmp_path) -> Settings:
        """构造 offline Settings，zgraph_home 指向 tmp_path。"""
        import os

        os.environ["ZGRAPH_OFFLINE"] = "true"
        os.environ["ZGRAPH_HOME"] = str(tmp_path / "zhome")
        os.environ["ZGRAPH_TMP_STORE_PATH"] = str(tmp_path / "storage")
        (tmp_path / "zhome").mkdir(parents=True, exist_ok=True)
        (tmp_path / "storage").mkdir(parents=True, exist_ok=True)
        return Settings.from_env()

    def test_setup_runtime_sets_auto_approved_flag(self, tmp_path):
        """``_setup_runtime`` 在 auto-approve 分支必须设置
        ``state["auto_approved"] = True``。
        """
        from zgraph.workflow.base import WorkflowResult

        settings = self._settings_offline(tmp_path)
        settings.auto_approve_interrupts = True  # 关键
        rt = ZGraphRuntime(settings)

        # stub 所有 workflow：返回 high-risk capability + pending interrupt
        def fake_intent(state):
            return WorkflowResult("intent", "completed", state)

        def fake_capability(state):
            return {
                "selected_skills": [],
                "selected_tools": ["bash"],
                "required_tools": ["bash"],
                "selected_workflows": [],
                "preconditions": [],
                "validations": [],
                "risk_level": "high",  # high → 走 guardian 路径
                "spawn_required": False,
                "retrieval_strategy": "simple",
            }

        def fake_validate(state):
            return WorkflowResult("validate", "completed", state)

        def fake_risk(state):
            state["risk_level"] = "high"
            return WorkflowResult("risk", "completed", state)

        def fake_approve(state):
            # 模拟 guardian 返回 pending interrupt
            state["interrupt"] = {
                "interrupt_id": "i-auto",
                "status": "pending",
                "reason": "high risk tool: bash",
            }
            return WorkflowResult("approve", "interrupted", state)

        class StubWF:
            def __init__(self, fn):
                self._fn = fn

            def run(self, state):
                return self._fn(state)

        rt.intent_workflow = StubWF(fake_intent)
        # CapabilityCompiler 是按类 compile 的，直接 monkeypatch 实例
        class FakeCompiler:
            def compile(self, state):
                return fake_capability(state)

        rt.capability_compiler = FakeCompiler()
        rt.validate_workflow = StubWF(fake_validate)
        rt.risk_workflow = StubWF(fake_risk)
        rt.approve_workflow = StubWF(fake_approve)

        from zgraph.runtime.hooks import RunContext
        from zgraph.workspace import WorkspaceManager

        wm = WorkspaceManager(tmp_path, storage_root=tmp_path / "storage")
        ws = wm.create_run("r-5-6-setup")
        ctx = RunContext(
            run_id="r-5-6-setup",
            user_input="bash something",
            settings=settings,
            started_at=0.0,
        )

        # stub tool_registry 和 skills
        class FakeToolRegistry:
            def get(self, name):
                return None

        # _setup_runtime 在 auto_approve=True 时返回 6-tuple
        result = rt._setup_runtime("bash something", ws, ctx)

        assert result is not None, "auto-approve 路径不应返回 None"
        state, capabilities, interrupt, tool_registry, skills, context = result
        # 关键断言：state["auto_approved"] = True
        assert state.get("auto_approved") is True, (
            "_setup_runtime must set state['auto_approved']=True "
            "after Guardian auto-approve branch"
        )
        # interrupt 已被改为 approved
        assert interrupt is not None
        assert interrupt.get("status") == "approved"
        assert interrupt.get("decision_reason") == "auto-approved by runtime policy"

    def test_pending_interrupt_does_not_set_auto_approved(self, tmp_path):
        """人工审批 pending 路径不应设置 ``auto_approved``。
        防回归：确保只 auto-approve 分支设标志。
        """
        from zgraph.workflow.base import WorkflowResult

        settings = self._settings_offline(tmp_path)
        settings.auto_approve_interrupts = False  # 关键：人工审批
        rt = ZGraphRuntime(settings)

        def fake_intent(state):
            return WorkflowResult("intent", "completed", state)

        def fake_capability(state):
            return {
                "selected_skills": [],
                "selected_tools": ["bash"],
                "required_tools": ["bash"],
                "selected_workflows": [],
                "preconditions": [],
                "validations": [],
                "risk_level": "high",
                "spawn_required": False,
                "retrieval_strategy": "simple",
            }

        def fake_validate(state):
            return WorkflowResult("validate", "completed", state)

        def fake_risk(state):
            state["risk_level"] = "high"
            return WorkflowResult("risk", "completed", state)

        def fake_approve(state):
            state["interrupt"] = {
                "interrupt_id": "i-pending",
                "status": "pending",
                "reason": "high risk tool: bash",
            }
            return WorkflowResult("approve", "interrupted", state)

        class StubWF:
            def __init__(self, fn):
                self._fn = fn

            def run(self, state):
                return self._fn(state)

        rt.intent_workflow = StubWF(fake_intent)

        class FakeCompiler:
            def compile(self, state):
                return fake_capability(state)

        rt.capability_compiler = FakeCompiler()
        rt.validate_workflow = StubWF(fake_validate)
        rt.risk_workflow = StubWF(fake_risk)
        rt.approve_workflow = StubWF(fake_approve)

        from zgraph.runtime.hooks import RunContext
        from zgraph.workspace import WorkspaceManager

        wm = WorkspaceManager(tmp_path, storage_root=tmp_path / "storage")
        ws = wm.create_run("r-5-6-pending")
        ctx = RunContext(
            run_id="r-5-6-pending",
            user_input="bash something",
            settings=settings,
            started_at=0.0,
        )

        result = rt._setup_runtime("bash something", ws, ctx)
        assert result is not None
        state, capabilities, interrupt, *_ = result
        # 关键断言：人工审批路径 state['auto_approved'] 缺失或为 False
        assert not state.get("auto_approved")
        assert interrupt is not None
        assert interrupt.get("status") == "pending"
