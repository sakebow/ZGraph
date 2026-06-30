"""Phase 5.2 / 5.3：runtime.run() 重新路由到 workflow / recommend / stream。

回归覆盖 code-review finding #2（workflow 路径消失）和 #3（recommend intent
消失）：

- ``exam-plan`` 等标了 ``workflow:`` 的 skill 走 ``runtime.run()`` 时必须执行
  deterministic workflow，而不是落到 LLM 流式 chat。
- intent 被分类为 ``recommend_questions`` 时，``runtime.run()`` 必须走
  ``_run_recommendation``，得到结构化推荐而不是 LLM 闲聊。
- 普通 chat prompt 走 astream 流式路径（向后兼容）。
"""

from __future__ import annotations

from typing import Any

import pytest

from zgraph.config import Settings
from zgraph.runtime import ZGraphRuntime


pytestmark = pytest.mark.integration


def _settings_offline(tmp_path, monkeypatch=None) -> Settings:
    """构造 offline Settings，zgraph_home 指向 tmp_path。

    必须通过 ``monkeypatch.setenv`` 设环境变量，否则 ``os.environ[...] = ...``
    直接改全局环境，会污染同进程后续测试。conftest 的 ``_isolate_provider_env``
    fixture 只清理 provider vars，不碰 ZGRAPH_HOME，所以这里必须用 monkeypatch。
    """
    if monkeypatch is None:
        raise ValueError(
            "_settings_offline requires monkeypatch to avoid leaking "
            "ZGRAPH_HOME / ZGRAPH_TMP_STORE_PATH to subsequent tests"
        )
    monkeypatch.setenv("ZGRAPH_OFFLINE", "true")
    monkeypatch.setenv("ZGRAPH_HOME", str(tmp_path / "zhome"))
    monkeypatch.setenv("ZGRAPH_TMP_STORE_PATH", str(tmp_path / "storage"))
    (tmp_path / "zhome").mkdir(parents=True, exist_ok=True)
    return Settings.from_env()


class TestRunDispatchesToStream:
    """普通 chat prompt：默认走 astream 流式。"""

    def test_run_chat_returns_completed_status(self, tmp_path, monkeypatch):
        settings = _settings_offline(tmp_path, monkeypatch)
        rt = ZGraphRuntime(settings)
        result = rt.run("hello", run_id="r-chat-1")
        # 不指定 workflow capability 的 chat 走流式，status 应该是 completed
        assert result.status == "completed"
        # content 不为空（offline 模式会有「ZGraph runtime is ready」之类文本）
        assert isinstance(result.content, str)


class TestRunDispatchesToRecommend:
    """Phase 5.3：intent=recommend_questions 时走 _run_recommendation。"""

    def test_run_routes_to_recommendation_when_intent_is_recommend(
        self, monkeypatch, tmp_path
    ):
        """用 monkeypatch 强制 IntentWorkflow 返回 recommend_questions，
        验证 ``run()`` 走 ``_run_recommendation`` 而不是 LLM 流式。
        """
        from zgraph.workflow.base import WorkflowResult

        settings = _settings_offline(tmp_path, monkeypatch)
        rt = ZGraphRuntime(settings)

        # Stub IntentWorkflow 让它强制返回 recommend_questions
        def fake_intent_run(state):
            state.update({
                "intent": {"name": "recommend_questions", "confidence": 1.0, "difficulty": "easy", "risk_hint": "low"},
                "hint": {
                    "summary": "recommend questions",
                    "domain": "conversation",
                    "task_type": "recommend_questions",
                    "keywords": ["recommend"],
                    "slots": {},
                    "candidate_workflows": ["recommend_questions"],
                    "candidate_tools": [],
                    "risk_signals": [],
                },
                "todo": [{"id": 1, "item": "load memory"}, {"id": 2, "item": "generate recommendations"}],
            })
            return WorkflowResult("intent", "completed", state)

        monkeypatch.setattr(rt.intent_workflow, "run", fake_intent_run)

        # Spy: 标记 _run_recommendation 被调用
        called = {"yes": False}
        original = rt._run_recommendation

        def spy_recommend(workspace, state):
            called["yes"] = True
            return original(workspace, state)

        monkeypatch.setattr(rt, "_run_recommendation", spy_recommend)

        result = rt.run("recommend something", run_id="r-rec-1")

        assert called["yes"], "run() should route to _run_recommendation when intent=recommend_questions"
        # recommend_questions workflow 返回结构化 data，content 是 JSON
        assert result.status == "completed"


class TestRunDispatchesToWorkflow:
    """Phase 5.2：selected_workflows 包含 temporary_workflow 时走 _execute 路径。"""

    def test_run_routes_to_workflow_when_capability_sets_temporary_workflow(
        self, monkeypatch, tmp_path
    ):
        """强制 CapabilityCompiler 返回 selected_workflows=['temporary_workflow']，
        验证 ``run()`` 走 ``_execute`` 而不是 LLM 流式。
        """
        settings = _settings_offline(tmp_path, monkeypatch)
        rt = ZGraphRuntime(settings)

        # Stub CapabilityCompiler 让它强制返回 temporary_workflow capability
        def fake_compile(state):
            return {
                "selected_skills": ["exam-plan"],
                "selected_tools": ["read"],
                "required_tools": ["read"],
                "selected_workflows": ["temporary_workflow"],
                "preconditions": [],
                "validations": [],
                "risk_level": "low",
                "spawn_required": False,
                "retrieval_strategy": "simple",
            }

        # 构造一个 fake tool_registry —— ``_selected_tools`` 需要 .get(name)
        class FakeToolRegistry:
            def get(self, name):
                return None  # 没真实工具，_selected_tools 返回 []

        fake_registry = FakeToolRegistry()
        fake_context = None  # _run_workflow_path 在 auto_approved=False 时不读 context

        monkeypatch.setattr(rt, "_setup_runtime", lambda ui, ws, ctx: (
            {"user_input": ui, "hint": {}, "intent": {}, "todo": []},
            fake_compile({}),
            None,
            fake_registry,  # tool_registry
            [],            # skills
            fake_context,  # context
        ))

        # Spy _execute
        called = {"yes": False}

        def spy_execute(*args, **kwargs):
            called["yes"] = True
            # 模拟 workflow 执行成功
            return "Workflow completed."

        monkeypatch.setattr(rt, "_execute", spy_execute)

        result = rt.run("plan my exam", run_id="r-wf-1")

        assert called["yes"], "run() should call _execute when capability.selected_workflows contains 'temporary_workflow'"
        # _execute 走同步路径，不消费事件流
        assert result.status in {"completed", "failed"}
        assert isinstance(result.content, str)


class TestRunFailsGracefullyWhenSetupRaises:
    """setup 失败时 run() 不抛异常，返回 status=failed。"""

    def test_run_returns_failed_when_setup_raises(self, monkeypatch, tmp_path):
        settings = _settings_offline(tmp_path, monkeypatch)
        rt = ZGraphRuntime(settings)

        def fake_setup_raises(user_input, workspace, ctx):
            raise RuntimeError("intent workflow blew up")

        monkeypatch.setattr(rt, "_setup_runtime", fake_setup_raises)

        result = rt.run("anything", run_id="r-bad-1")
        assert result.status == "failed"
        assert "intent workflow blew up" in (result.error or "")
