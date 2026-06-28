"""Tests for RuntimeHook 体系（Phase 2）。

覆盖：
- 钩子链顺序
- drop 语义（return None）
- 异常隔离（单个 hook 抛错不破坏主流程）
- RunContext.metadata 跨 hook 传递
- 内置钩子：AuditHook / MetricsHook / PIIFilterHook
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from zgraph.config import Settings
from zgraph.runtime.events import ContentDelta, Final, ReasoningDelta, RuntimeEvent
from zgraph.runtime.hooks import RunContext, RuntimeHook
from zgraph.runtime.hooks.builtin import AuditHook, MetricsHook, PIIFilterHook


pytestmark = pytest.mark.integration


def _settings() -> Settings:
    """构造一个最小可用的 Settings 实例（不依赖任何 env 变量）。"""
    return Settings.from_env()  # conftest 已经清理过 provider 相关 env


# ---------------------------------------------------------------------------
# 基础：RunContext + hook 协议
# ---------------------------------------------------------------------------


class TestRunContext:
    def test_construction_defaults(self):
        ctx = RunContext(
            run_id="r1",
            user_input="hi",
            settings=_settings(),
            started_at=0.0,
        )
        assert ctx.run_id == "r1"
        assert ctx.metadata == {}

    def test_metadata_is_mutable_per_run(self):
        ctx = RunContext(
            run_id="r1",
            user_input="hi",
            settings=_settings(),
            started_at=0.0,
        )
        ctx.metadata["k"] = "v"
        assert ctx.metadata["k"] == "v"


class TestRuntimeHookProtocol:
    def test_runtime_checkable(self):
        # Protocol 是 runtime_checkable，isinstance 应可用
        class MyHook:
            async def __call__(self, event, ctx):
                return event

        h = MyHook()
        assert isinstance(h, RuntimeHook)


# ---------------------------------------------------------------------------
# MetricsHook：累计计数
# ---------------------------------------------------------------------------


class TestMetricsHook:
    async def test_counts_content_and_reasoning(self):
        ctx = RunContext(
            run_id="r1",
            user_input="hi",
            settings=_settings(),
            started_at=0.0,
        )
        hook = MetricsHook()
        await hook(ContentDelta(text="hello"), ctx)
        await hook(ContentDelta(text=" world"), ctx)
        await hook(ReasoningDelta(text="think"), ctx)
        m = ctx.metadata["metrics"]
        assert m["content_delta_count"] == 2
        assert m["content_chars"] == 11
        assert m["reasoning_delta_count"] == 1
        assert m["reasoning_chars"] == 5


# ---------------------------------------------------------------------------
# PIIFilterHook
# ---------------------------------------------------------------------------


class TestPIIFilterHook:
    async def test_email_masked(self):
        ctx = RunContext(run_id="r1", user_input="", settings=_settings(), started_at=0.0)
        hook = PIIFilterHook()
        out = await hook(ContentDelta(text="contact me at alice@example.com please"), ctx)
        assert out is not None
        assert "alice@example.com" not in out.text
        assert "[EMAIL]" in out.text

    async def test_phone_masked(self):
        ctx = RunContext(run_id="r1", user_input="", settings=_settings(), started_at=0.0)
        hook = PIIFilterHook()
        out = await hook(ContentDelta(text="call 13800138000"), ctx)
        assert "13800138000" not in out.text
        assert "[CN_PHONE]" in out.text

    async def test_clean_text_unchanged(self):
        ctx = RunContext(run_id="r1", user_input="", settings=_settings(), started_at=0.0)
        hook = PIIFilterHook()
        out = await hook(ContentDelta(text="just a normal sentence"), ctx)
        assert out.text == "just a normal sentence"

    async def test_disabled(self):
        ctx = RunContext(run_id="r1", user_input="", settings=_settings(), started_at=0.0)
        hook = PIIFilterHook(enabled=False)
        out = await hook(ContentDelta(text="alice@example.com"), ctx)
        assert out.text == "alice@example.com"

    async def test_non_content_event_passthrough(self):
        ctx = RunContext(run_id="r1", user_input="", settings=_settings(), started_at=0.0)
        hook = PIIFilterHook()
        e = ReasoningDelta(text="alice@example.com")  # 不会 mask
        out = await hook(e, ctx)
        assert out is e


# ---------------------------------------------------------------------------
# AuditHook：写文件
# ---------------------------------------------------------------------------


class TestAuditHook:
    async def test_writes_ndjson_on_final(self, tmp_path: Path):
        audit_file = tmp_path / "audit.json"
        ctx = RunContext(
            run_id="r1",
            user_input="hi",
            settings=_settings(),
            started_at=0.0,
        )
        hook = AuditHook(path=audit_file)
        rt = {
            "run_id": "r1",
            "status": "completed",
            "content": "hello",
            "reasoning_content": "",
            "interrupt": None,
            "error": None,
        }
        await hook(Final(run_id="r1", status="completed", finish_reason="stop", runtime_result=rt), ctx)
        assert audit_file.exists()
        lines = audit_file.read_text(encoding="utf-8").strip().split("\n")
        assert len(lines) == 1
        record = json.loads(lines[0])
        assert record["run_id"] == "r1"
        assert record["status"] == "completed"

    async def test_non_final_event_passthrough(self, tmp_path: Path):
        audit_file = tmp_path / "audit.json"
        ctx = RunContext(run_id="r1", user_input="", settings=_settings(), started_at=0.0)
        hook = AuditHook(path=audit_file)
        out = await hook(ContentDelta(text="hello"), ctx)
        assert out.text == "hello"
        assert not audit_file.exists()


# ---------------------------------------------------------------------------
# 链式行为：drop / 异常隔离 / metadata 传递
# ---------------------------------------------------------------------------


class _CountingHook:
    def __init__(self, name: str, counter_key: str):
        self.name = name
        self.counter_key = counter_key

    async def __call__(self, event, ctx):
        ctx.metadata.setdefault("chain_order", []).append(self.name)
        ctx.metadata[self.counter_key] = ctx.metadata.get(self.counter_key, 0) + 1
        return event


class _DropHook:
    async def __call__(self, event, ctx):
        return None  # drop everything


class _ModifyHook:
    def __init__(self, prefix: str):
        self.prefix = prefix

    async def __call__(self, event, ctx):
        if isinstance(event, ContentDelta):
            return ContentDelta(text=self.prefix + event.text)
        return event


class _BoomHook:
    async def __call__(self, event, ctx):
        raise RuntimeError("boom from hook")


class TestHookChain:
    async def test_chain_order(self):
        ctx = RunContext(run_id="r1", user_input="", settings=_settings(), started_at=0.0)
        h1 = _CountingHook("h1", "k1")
        h2 = _CountingHook("h2", "k2")

        for h in [h1, h2]:
            await h(ContentDelta(text="x"), ctx)
        assert ctx.metadata["chain_order"] == ["h1", "h2"]
        assert ctx.metadata["k1"] == 1
        assert ctx.metadata["k2"] == 1

    async def test_drop_short_circuits_subsequent_hooks(self):
        ctx = RunContext(run_id="r1", user_input="", settings=_settings(), started_at=0.0)
        drop = _DropHook()
        counter = _CountingHook("counter", "k")

        # Manually simulate the runtime's _apply_hooks loop
        event = ContentDelta(text="hi")
        current = event
        for h in [drop, counter]:
            try:
                result = await h(current, ctx)
            except Exception:
                continue
            if result is None:
                current = None
                break
            current = result
        # drop 后 current 是 None，counter 没被调用
        assert current is None
        assert "k" not in ctx.metadata

    async def test_modify_changes_event(self):
        ctx = RunContext(run_id="r1", user_input="", settings=_settings(), started_at=0.0)
        mod = _ModifyHook("[redacted]")
        current = ContentDelta(text="alice@example.com")
        current = await mod(current, ctx)
        assert current.text == "[redacted]alice@example.com"

    async def test_boom_is_isolated(self):
        """单个 hook 抛错不影响主流程。"""
        ctx = RunContext(run_id="r1", user_input="", settings=_settings(), started_at=0.0)
        boom = _BoomHook()
        counter = _CountingHook("counter", "k")

        # Runtime 的 _apply_hooks 模拟
        current = ContentDelta(text="hi")
        for h in [boom, counter]:
            try:
                result = await h(current, ctx)
            except Exception:
                continue  # boom 跳过
            if result is None:
                current = None
                break
            current = result
        # counter 仍然被调用，metadata 写进去了
        assert current is not None
        assert ctx.metadata["k"] == 1


# ---------------------------------------------------------------------------
# Runtime 集成：astream 默认注册 3 个钩子
# ---------------------------------------------------------------------------


class TestRuntimeHookIntegration:
    async def test_runtime_registers_default_hooks(self):
        from zgraph.runtime import ZGraphRuntime

        rt = ZGraphRuntime(_settings())
        names = [type(h).__name__ for h in rt.hooks]
        assert "AuditHook" in names
        assert "MetricsHook" in names
        assert "PIIFilterHook" in names

    async def test_runtime_accepts_custom_hooks(self):
        from zgraph.runtime import ZGraphRuntime

        class Noop:
            async def __call__(self, event, ctx):
                return event

        rt = ZGraphRuntime(_settings(), hooks=[Noop()])
        assert len(rt.hooks) == 1
        assert isinstance(rt.hooks[0], Noop)
