"""Phase 2.3 hooks/registry.py 测试。"""

from __future__ import annotations

import pytest

from zgraph.config import Settings
from zgraph.runtime import ZGraphRuntime
from zgraph.runtime.hooks import RuntimeHook, default_hooks
from zgraph.runtime.hooks.builtin import AuditHook, MetricsHook, PIIFilterHook
from zgraph.runtime.hooks.guardian_hook import GuardianHook
from zgraph.runtime.hooks.registry import default_hooks as registry_default_hooks


pytestmark = pytest.mark.integration


class TestDefaultHooks:
    def test_returns_four_built_in_hooks(self):
        """default_hooks() 返回 4 个内置 hook：Audit / Metrics / PIIFilter / Guardian。"""
        hooks = default_hooks()
        assert len(hooks) == 4
        assert isinstance(hooks[0], AuditHook)
        assert isinstance(hooks[1], MetricsHook)
        assert isinstance(hooks[2], PIIFilterHook)
        assert isinstance(hooks[3], GuardianHook)

    def test_returns_new_list_each_call(self):
        """每次调用都返回新的 list（避免共享同一份实例的状态污染）。"""
        a = default_hooks()
        b = default_hooks()
        assert a is not b
        assert a[0] is not b[0]

    def test_all_hooks_satisfy_runtime_hook_protocol(self):
        """每个默认 hook 都是 RuntimeHook 的实例。"""
        for h in default_hooks():
            assert isinstance(h, RuntimeHook), f"{type(h).__name__} is not RuntimeHook"

    def test_registry_and_top_level_alias_match(self):
        """registry.default_hooks 和 zgraph.runtime.hooks.default_hooks 是同一函数。"""
        assert default_hooks is registry_default_hooks


class TestRuntimeUsesRegistry:
    def test_runtime_uses_default_hooks_when_none_passed(self):
        """Runtime(hooks=None) → 自动调 default_hooks()。"""
        rt = ZGraphRuntime(Settings.from_env(), hooks=None)
        names = [type(h).__name__ for h in rt.hooks]
        assert names == ["AuditHook", "MetricsHook", "PIIFilterHook", "GuardianHook"]

    def test_runtime_uses_empty_list_when_empty_passed(self):
        """Runtime(hooks=[]) → 空列表（不要 fallback 到 default）。"""
        rt = ZGraphRuntime(Settings.from_env(), hooks=[])
        assert rt.hooks == []

    def test_runtime_uses_custom_hooks(self):
        """Runtime(hooks=[...]) → 用调用方传入的列表。"""

        class Noop:
            async def __call__(self, event, ctx):
                return event

        rt = ZGraphRuntime(Settings.from_env(), hooks=[Noop()])
        assert len(rt.hooks) == 1
        assert isinstance(rt.hooks[0], Noop)

    def test_runtime_supports_appending_to_defaults(self):
        """常见用法：Runtime(hooks=[*default_hooks(), MyHook()])。"""
        seen: list[str] = []

        class Tracker:
            async def __call__(self, event, ctx):
                seen.append(type(event).__name__)
                return event

        rt = ZGraphRuntime(
            Settings.from_env(),
            hooks=[*default_hooks(), Tracker()],
        )
        assert len(rt.hooks) == 5
        assert isinstance(rt.hooks[-1], Tracker)
