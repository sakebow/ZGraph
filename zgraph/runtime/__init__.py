from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from dataclasses import dataclass, field, replace
from typing import Any, AsyncIterator

from zgraph.capability import CapabilityCompiler
from zgraph.config import Settings
from zgraph.core.agent.manager import AgentManager
from zgraph.core.agent.runner import AgentResult
from zgraph.runtime.events import (
    ContentDelta,
    Final,
    Interrupt,
    MediaReady,
    ReasoningDelta,
    RuntimeEvent,
    ToolCallArgs,
    ToolCallEnd,
    ToolCallStart,
)
from zgraph.core.memory.compressor import MemoryCompressor
from zgraph.core.memory.loader import MemoryLoader
from zgraph.core.memory.saver.jsonl_saver import JsonlMemorySaver
from zgraph.core.skills.loader import SkillLoader
from zgraph.core.tool.base import ToolContext, RuntimeTool
from zgraph.core.tool.builder import build_default_tool_registry
from zgraph.middleware.base import MiddlewareChain
from zgraph.middleware.exceptions import ExceptionMiddleware
from zgraph.middleware.limit import RateLimitMiddleware
from zgraph.middleware.logger import LoggerMiddleware
from zgraph.workflow.guardian.approve import ApproveWorkflow
from zgraph.workflow.guardian.risk import RiskWorkflow
from zgraph.workflow.guardian.validate import ValidateWorkflow
from zgraph.workflow.builder import WorkflowBuilder
from zgraph.workflow.executor import WorkflowExecutor
from zgraph.workflow.planner import TemporaryWorkflowPlanner, TemporaryWorkflowReviewer
from zgraph.workflow.registry import WorkflowDefinition, WorkflowRegistry
from zgraph.workflow.slots import WorkflowSlotResolver
from zgraph.workflow.service.fix import FixWorkflow
from zgraph.workflow.service.intent import IntentWorkflow
from zgraph.workflow.service.recommend import RecommendQuestionsWorkflow
from zgraph.workflow.spec import validate_workflow_spec
from zgraph.workspace import WorkspaceManager, RunWorkspace


@dataclass(slots=True)
class RuntimeResult:

    """运行时结果。"""
    run_id: str
    status: str
    content: str
    hint: dict[str, Any] = field(default_factory=dict)
    intent: dict[str, Any] = field(default_factory=dict)
    todo: list[dict[str, Any]] = field(default_factory=list)
    capabilities: dict[str, Any] = field(default_factory=dict)
    interrupt: dict[str, Any] | None = None
    artifacts: list[str] = field(default_factory=list)
    error: str | None = None
    data: dict[str, Any] | None = None

    # —— Phase 1 / 3 新增 —— 后向兼容：默认值保证现有调用者零改动
    reasoning_content: str = ""
    """thinking 模型的推理内容。从 AIMessage.additional_kwargs['reasoning_content'] 提取。"""

    media: list[dict[str, Any]] = field(default_factory=list)
    """本次 run 产出的媒体清单。每项 dict 来自 MediaReady 的可序列化形态。"""

    interrupt_token: str | None = None
    """中断时的恢复 token。供后续 /resume 鉴权使用。"""

    def to_dict(self) -> dict[str, Any]:
        """转字典"""
        return {
            "run_id": self.run_id,
            "status": self.status,
            "content": self.content,
            "hint": self.hint,
            "intent": self.intent,
            "todo": self.todo,
            "capabilities": self.capabilities,
            "interrupt": self.interrupt,
            "artifacts": self.artifacts,
            "error": self.error,
            "data": self.data,
            "reasoning_content": self.reasoning_content,
            "media": self.media,
            "interrupt_token": self.interrupt_token,
        }


class ZGraphRuntime:

    """zgraph-runtime"""
    def __init__(
        self,
        settings: Settings | None = None,
        *,
        hooks: list[Any] | None = None,
    ) -> None:
        """初始化实例属性

        参数:
            settings: 运行时配置（Settings | None）。None 时从环境加载。
            hooks: RuntimeHook 列表，按声明顺序串联在事件流上（list | None）。
        """
        self.settings = settings or Settings.from_env()
        # Phase 3.4 收尾：storage_root 与 media_store 写到同一棵目录树（默认
        # settings.tmp_store_path），避免 ``workspace.storage_dir`` 写文件而
        # ``emit_media`` 写到别处的分家问题。
        self.workspace_manager = WorkspaceManager(
            self.settings.zgraph_home,
            storage_root=self.settings.tmp_store_path,
        )
        self.agent_manager = AgentManager(self.settings)
        self.intent_workflow = IntentWorkflow(self.settings)
        self.validate_workflow = ValidateWorkflow()
        self.risk_workflow = RiskWorkflow()
        self.approve_workflow = ApproveWorkflow()
        self.workflow_builder = WorkflowBuilder()
        self.workflow_registry = WorkflowRegistry(zgraph_home=self.settings.zgraph_home, skills_dir=self.settings.skills_dir)
        self.fix_workflow = FixWorkflow(self.settings)
        self.workflow_slot_resolver = WorkflowSlotResolver(self.settings, self.fix_workflow)
        self.memory_path = self.settings.data_dir / "memory.jsonl"
        self.memory_loader = MemoryLoader(self.memory_path)
        self.memory_saver = JsonlMemorySaver(self.memory_path)
        self.recommend_workflow = RecommendQuestionsWorkflow(self.settings, self.memory_loader)
        self.memory_compressor = MemoryCompressor()
        # Phase 2.3：默认钩子链走 registry.py，便于统一管理和测试。
        # 不传 hooks= 时，Runtime 会自动用 ``default_hooks()`` 初始化。
        from zgraph.runtime.hooks.registry import default_hooks

        self.hooks: list[Any] = list(hooks) if hooks is not None else default_hooks()

        # Phase 3：媒体存储
        from zgraph.runtime.media_storage import get_media_storage

        self.media_store = get_media_storage(self.settings)
        # Phase 3.5：per-run 待聚合 MediaReady 事件缓存。
        # ``emit_media(run_id=...)`` 把事件压入此 dict；``astream()`` 在收尾时
        # 把它消费掉，yield MediaReady + 写进 RuntimeResult.media，然后清空。
        self._pending_media: dict[str, list[MediaReady]] = {}

        self.logger = logging.getLogger("zgraph")
        self._configure_logging()

    def _configure_logging(self) -> None:
        if not self.settings.log_enabled:
            logging.getLogger("zgraph").disabled = True
            return
        logging.basicConfig(
            level=getattr(logging, self.settings.log_level.upper(), logging.INFO),
            format="%(asctime)s %(levelname)s %(name)s %(message)s",
        )

    def _skill_dirs(self) -> list[Any]:
        return [self.settings.zgraph_home / "apps", self.settings.skills_dir]

    async def _apply_hooks(
        self, event: "RuntimeEvent", ctx: Any
    ) -> "RuntimeEvent | None":
        """让 event 按顺序过每个 hook。单个 hook 抛错被隔离。

        参数:
            event: 待处理的事件（RuntimeEvent）。
            ctx: per-run 上下文（RunContext）。

        返回:
            处理后的事件；如果被某个 hook drop（return None）则为 None。
        """
        current = event
        for hook in self.hooks:
            try:
                result = await hook(current, ctx)
            except Exception as exc:
                self.logger.warning(
                    "hook %s raised on event %s: %s",
                    type(hook).__name__,
                    type(current).__name__,
                    exc,
                )
                continue
            if result is None:
                # hook 显式 drop；后续 hook 不再处理
                return None
            current = result
        return current

    async def astream(
        self, user_input: str, *, run_id: str | None = None
    ) -> AsyncIterator[RuntimeEvent]:
        """异步流式入口。最后一个事件永远是 Final。

        实现：sync 跑 intent / capability / guardian（这些 LLM 调用耗时短），
        然后用 LangChain ``agent.astream_events`` 流式产出 agent 阶段的事件。
        workflow / offline 路径暂走 sync，通过单个 Final 事件交付。

        所有事件先过 ``self.hooks`` 链再 yield（Phase 2）。

        参数:
            user_input: 用户输入的原始文本（str）。
            run_id: 可选的运行标识符（str | None）。

        异常:
            不会向外抛异常；失败转译为 ``status='failed'`` 的 Final 事件。
        """
        workspace = self.workspace_manager.create_run(run_id)
        # Phase 2：构造 per-run 上下文供 hooks 共享状态
        from zgraph.runtime.hooks import RunContext

        ctx = RunContext(
            run_id=workspace.run_id,
            user_input=user_input,
            settings=self.settings,
            started_at=time.time(),
        )

        async def _gen():
            """内部 generator，把所有 yield 集中在一处。

            用 try/finally 兜底：所有路径（成功 / 失败 / interrupted / 异常）
            都会把 ``_pending_media[workspace.run_id]`` 弹掉，避免内存泄漏。
            失败路径在 yield _failed_final 前先把累积的 media events 一起 yield
            出去（通过 ``_with_pending_media`` 附加到 Final.runtime_result.media）。
            """
            try:
                # —— 1. sync pre-flight：setup / intent / capability ——
                try:
                    context = ToolContext(
                        workspace=workspace,
                        allow_bash=self.settings.allow_bash,
                        emit_media=self._make_emit_media(workspace.run_id),
                        metadata={"zgraph_home": str(self.settings.zgraph_home)},
                    )
                    tool_registry = build_default_tool_registry(context)
                    skills = SkillLoader(self._skill_dirs()).load()
                    state: dict[str, Any] = {
                        "run_id": workspace.run_id,
                        "user_input": user_input,
                        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                    }
                    intent_result = self.intent_workflow.run(state)
                    state.update(intent_result.data)
                    compiler = CapabilityCompiler(self.settings, tool_registry, skills)
                    capabilities = compiler.compile(state)
                    state["capabilities"] = capabilities
                except Exception as exc:
                    yield self._with_pending_media(
                        self._failed_final(workspace.run_id, f"setup failed: {exc}"),
                        workspace.run_id,
                    )
                    return

                # —— 2. Guardian（同步；interrupted 路径 emit Final 而非走 stream）——
                interrupt: dict[str, Any] | None = None
                if capabilities.get("risk_level") in {"medium", "high"}:
                    validation = self.validate_workflow.run(state)
                    if validation.status != "completed":
                        yield self._with_pending_media(
                            self._failed_final(
                                workspace.run_id,
                                "; ".join(validation.errors) or "Guardian validation failed",
                                state=state,
                                capabilities=capabilities,
                            ),
                            workspace.run_id,
                        )
                        return
                    risk = self.risk_workflow.run(state)
                    capabilities["risk_level"] = risk.data["risk_level"]
                    state["risk_level"] = capabilities["risk_level"]
                    approval = self.approve_workflow.run(state)
                    if approval.status == "interrupted":
                        interrupt = approval.data["interrupt"]
                        if not self.settings.auto_approve_interrupts:
                            yield self._with_pending_media(
                                Final(
                                    run_id=workspace.run_id,
                                    status="interrupted",
                                    finish_reason="interrupted",
                                    runtime_result=RuntimeResult(
                                        run_id=workspace.run_id,
                                        status="interrupted",
                                        content="High risk task requires explicit approval.",
                                        hint=state.get("hint", {}),
                                        intent=state.get("intent", {}),
                                        todo=state.get("todo", []),
                                        capabilities=capabilities,
                                        interrupt=interrupt,
                                        interrupt_token=interrupt.get("interrupt_id", ""),
                                    ),
                                ),
                                workspace.run_id,
                            )
                            return
                        interrupt["status"] = "approved"
                        interrupt["decision_reason"] = "auto-approved by runtime policy"
                        state["interrupt"] = interrupt

                selected_tools = self._selected_tools(capabilities, tool_registry)

                # —— 3. offline / 无 api_key：直接 Final ——
                if self.settings.offline or not self.settings.api_key:
                    offline_text = self._offline_execute(user_input, workspace, state)
                    yield self._with_pending_media(
                        Final(
                            run_id=workspace.run_id,
                            status="completed",
                            finish_reason="stop",
                            runtime_result=RuntimeResult(
                                run_id=workspace.run_id,
                                status="completed",
                                content=str(offline_text),
                                hint=state.get("hint", {}),
                                intent=state.get("intent", {}),
                                todo=state.get("todo", []),
                                capabilities=capabilities,
                                interrupt=interrupt,
                            ),
                        ),
                        workspace.run_id,
                    )
                    return

                # —— 4. 真流式：LangChain astream_events ——
                try:
                    agent = self.agent_manager.factory.create(
                        selected_tools,
                        system_prompt=self._system_prompt_with_skills(state, skills),
                    )
                except Exception as exc:
                    yield self._with_pending_media(
                        self._failed_final(workspace.run_id, f"agent create failed: {exc}"),
                        workspace.run_id,
                    )
                    return

                content_parts: list[str] = []
                reasoning_parts: list[str] = []
                try:
                    async for lc_event in agent.astream_events(
                        {"messages": [{"role": "user", "content": user_input}]},
                        version="v2",
                    ):
                        kind = lc_event.get("event")
                        if kind == "on_chat_model_stream":
                            chunk = lc_event.get("data", {}).get("chunk")
                            if chunk is None:
                                continue
                            text = getattr(chunk, "content", "") or ""
                            if text:
                                content_parts.append(text)
                                yield ContentDelta(text=text)
                            extra = getattr(chunk, "additional_kwargs", None) or {}
                            reasoning = extra.get("reasoning_content", "") or ""
                            if reasoning:
                                reasoning_parts.append(reasoning)
                                yield ReasoningDelta(text=reasoning)
                        elif kind == "on_tool_start":
                            yield ToolCallStart(
                                tool_call_id=str(lc_event.get("run_id", "")),
                                tool_name=str(lc_event.get("name", "")),
                            )
                        elif kind == "on_tool_end":
                            yield ToolCallEnd(
                                tool_call_id=str(lc_event.get("run_id", "")),
                                tool_name=str(lc_event.get("name", "")),
                                result=lc_event.get("data", {}).get("output"),
                                is_error=lc_event.get("data", {}).get("error") is not None,
                            )
                except Exception as exc:
                    yield self._with_pending_media(
                        self._failed_final(workspace.run_id, f"streaming execution failed: {exc}"),
                        workspace.run_id,
                    )
                    return

                full_content = "".join(content_parts)
                full_reasoning = "".join(reasoning_parts)

                # —— 5. Phase 3.5：弹出 per-run 待聚合的 MediaReady 事件 ——
                # 在 Final 之前逐条 yield，让客户端按事件流收到 URL；
                # 同时把全部事件的 dict 形态塞进 RuntimeResult.media，给同步消费者。
                media_events = self._consume_media(workspace.run_id)
                for media_event in media_events:
                    yield media_event

                # —— 6. 持久化 conversation.json（offload 到 executor，避免阻塞 event loop）——
                try:
                    loop = asyncio.get_running_loop()
                    await loop.run_in_executor(
                        None,
                        lambda: self._write_streaming_conversation(
                            workspace, user_input, full_content, full_reasoning
                        ),
                    )
                except Exception:
                    pass  # 持久化失败不影响最终事件

                yield Final(
                    run_id=workspace.run_id,
                    status="completed",
                    finish_reason="stop",
                    runtime_result=RuntimeResult(
                        run_id=workspace.run_id,
                        status="completed",
                        content=full_content,
                        hint=state.get("hint", {}),
                        intent=state.get("intent", {}),
                        todo=state.get("todo", []),
                        capabilities=capabilities,
                        interrupt=interrupt,
                        reasoning_content=full_reasoning,
                        media=[m.to_dict() for m in media_events],
                    ),
                )
            finally:
                # Defensive: 即便上面某个 yield 抛异常（罕见，比如 hook 抛错冒上来），
                # 也要保证 _pending_media 不残留。否则长跑 server 累积内存泄漏。
                self._pending_media.pop(workspace.run_id, None)

        # 用 async generator 代理 _gen()，每个事件过 _apply_hooks 后再 yield
        async for event in _gen():
            processed = await self._apply_hooks(event, ctx)
            if processed is not None:
                yield processed

    def _failed_final(
        self,
        run_id: str,
        error: str,
        *,
        state: dict[str, Any] | None = None,
        capabilities: dict[str, Any] | None = None,
    ) -> Final:
        """构造失败的 Final 事件（astream 内部 helper）。"""
        return Final(
            run_id=run_id,
            status="failed",
            finish_reason="error",            runtime_result=RuntimeResult(
                run_id=run_id,
                status="failed",
                content="",
                error=error,
                hint=(state or {}).get("hint", {}),
                intent=(state or {}).get("intent", {}),
                todo=(state or {}).get("todo", []),
                capabilities=capabilities or {},
            ),
        )

    def _write_streaming_conversation(
        self,
        workspace: RunWorkspace,
        user_input: str,
        content: str,
        reasoning_content: str,
    ) -> None:
        """astream 路径下写 conversation.json（精简版，不含中间消息）。"""
        path = workspace.logs_dir / "conversation.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "created_at": time.time(),
            "user_input": user_input,
            "output": content,
            "reasoning_content": reasoning_content,
            "source": "astream",
        }
        path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def emit_media(
        self,
        *,
        run_id: str,
        modality: str,
        mime: str,
        data: bytes,
        name: str,
        metadata: dict[str, Any] | None = None,
    ) -> MediaReady:
        """Phase 3：把媒体数据存到 media_store，返回 MediaReady 事件。

        供工具实现调用：产生图片/音频/视频/文件时，把字节内容交给这个方法，
        会写到 ``{tmp_store_path}/{run_id}/{name}`` 并返回带 URL 的事件。

        参数:
            run_id: 本次运行的唯一标识符（str）。
            modality: image / audio / video / file（str）。
            mime: MIME 类型（str）。
            data: 字节内容（bytes）。
            name: 文件名（不含路径），如 ``output.png``（str）。
            metadata: 附加元数据（dict | None），如宽高 / 时长。

        返回:
            MediaReady 事件（含 URL）。
        """
        url = self.media_store.put(
            run_id=run_id,
            name=name,
            data=data,
            mime=mime,
        )
        block_id = f"{modality}-{uuid.uuid4().hex[:8]}"
        # 计算 expires_at：now + ttl
        expires_at = time.strftime(
            "%Y-%m-%dT%H:%M:%SZ",
            time.gmtime(time.time() + self.settings.media_ttl_seconds),
        )
        event = MediaReady(
            block_id=block_id,
            modality=modality,
            mime=mime,
            url=url,
            size_bytes=len(data),
            metadata=dict(metadata or {}),
            expires_at=expires_at,
        )
        # Phase 3.5：压入 per-run 队列，等 astream() 收尾时聚合。
        # run_id 通常是 UUID，重入概率极低；如果发生，追加到现有 list。
        self._pending_media.setdefault(run_id, []).append(event)
        return event

    def _consume_media(self, run_id: str) -> list[MediaReady]:
        """astream() 收尾时调用：弹出该 run 的所有 MediaReady 事件。

        返回:
            该 run 期间累积的所有 MediaReady；并清空队列。
        """
        return self._pending_media.pop(run_id, [])

    def _with_pending_media(self, final: "Final", run_id: str) -> "Final":
        """把 ``_pending_media[run_id]`` 弹出并塞进 ``final.runtime_result.media``。

        用于 _gen() 各失败 / interrupted 路径：在 yield Final 之前调用，
        把工具已经 emit 但未 yield 给客户端的 MediaReady 事件附加上去。
        若 final 没有 runtime_result 或 pending 为空，则原样返回。

        返回:
            携带 media 字段的 Final 事件（runtime_result 是新对象）。
        """
        if final.runtime_result is None:
            return final
        media_events = self._consume_media(run_id)
        if not media_events:
            return final
        return replace(
            final,
            runtime_result=replace(
                final.runtime_result,
                media=[m.to_dict() for m in media_events],
            ),
        )

    def _make_emit_media(self, run_id: str):
        """构造绑定到指定 run_id 的 emit_media 回调，供 ToolContext 使用。

        tools 在 ``self.context.emit_media(...)`` 调用时，会触发 media_store.put
        并把 MediaReady 事件压入 ``runtime._pending_media[run_id]``，等 astream
        收尾时统一 yield。
        """

        def _emit(
            *,
            modality: str,
            mime: str,
            data: bytes,
            name: str,
            metadata: dict[str, Any] | None = None,
        ) -> MediaReady:
            return self.emit_media(
                run_id=run_id,
                modality=modality,
                mime=mime,
                data=data,
                name=name,
                metadata=metadata,
            )

        return _emit

    def cleanup_expired_media(self) -> int:
        """Phase 3.7：清理过期媒体，返回删除条数。"""
        try:
            return self.media_store.cleanup_expired(self.settings.media_ttl_seconds)
        except Exception as exc:
            self.logger.warning("media cleanup failed: %s", exc)
            return 0

    def list_available_examples(self) -> list[str]:
        """扫描 ``<zgraph_home>/storage/examples/``，返回可用媒体文件路径列表。

        给上层（HTTP/CLI handler）拼 system hint 用，让 LLM 看到 examples 目录里
        的本地文件路径，从而在收到「发我一张图片」这类模糊 prompt 时知道可以调
        ``media_input(path=...)`` 加载哪一张。

        返回绝对路径字符串列表，按文件名排序；目录不存在或为空时返回 ``[]``。
        """
        examples_dir = self.settings.zgraph_home / "storage" / "examples"
        if not examples_dir.is_dir():
            return []
        try:
            entries = sorted(
                entry.resolve()
                for entry in examples_dir.iterdir()
                if entry.is_file()
            )
        except OSError as exc:
            self.logger.warning("list_available_examples failed: %s", exc)
            return []
        return [str(path) for path in entries]

    def build_examples_hint(self) -> str:
        """把 :meth:`list_available_examples` 拼成 system hint 文本。

        没有 example 时返回空串，调用方应据此跳过注入；避免在 LLM 上下文里
        出现空段。
        """
        paths = self.list_available_examples()
        if not paths:
            return ""
        lines = [
            "Available example media files (use media_input tool to attach):",
        ]
        lines.extend(f"- {path}" for path in paths)
        return "\n".join(lines)

    def run_via_stream(self, user_input: str, *, run_id: str | None = None) -> RuntimeResult:
        """Phase 1.5：同步薄包装。跑 ``astream()``，用 ``StreamAggregator`` 把事件聚合成 RuntimeResult。

        与 ``run()`` 的区别：
        - ``run()`` 走 sync ``_execute`` + LangChain ``agent.invoke``，不消费事件流；
        - ``run_via_stream()`` 走 async ``astream()``，能拿到 reasoning_content /
          完整 tool_calls / 实时 media。

        注意：因为 astream() 里用 ``asyncio.run(_drain())`` 起新 loop，**不能在已有
        asyncio loop 的上下文里调用**（CLI 同步入口可以，HTTP server 端不行 ——
        HTTP server 自己有 per-request loop）。
        """
        import asyncio
        from zgraph.runtime.stream_aggregator import StreamAggregator

        events: list[RuntimeEvent] = []

        async def _drain() -> None:
            async for event in self.astream(user_input, run_id=run_id):
                events.append(event)

        asyncio.run(_drain())
        return StreamAggregator.collect(events)

    def run(self, user_input: str, *, run_id: str | None = None) -> RuntimeResult:
        workspace = self.workspace_manager.create_run(run_id)
        request = {"user_input": user_input, "run_id": workspace.run_id}

        chain = MiddlewareChain(
            [
                ExceptionMiddleware(debug=False),
                RateLimitMiddleware(max_calls=120, period_seconds=60),
                LoggerMiddleware(self.logger),
            ],
            lambda payload: self._run_unprotected(payload, workspace),
        )
        response = chain(request)
        if response.get("status") == "failed" and "content" not in response:
            return RuntimeResult(
                run_id=workspace.run_id,
                status="failed",
                content="",
                error=response.get("error"),
            )
        return RuntimeResult(**response)

    def _run_unprotected(self, request: dict[str, Any], workspace: RunWorkspace) -> dict[str, Any]:
        user_input = str(request.get("user_input", ""))
        self.logger.info("run_id=%s stage=setup:start", workspace.run_id)
        context = ToolContext(
            workspace=workspace,
            allow_bash=self.settings.allow_bash,
            emit_media=self._make_emit_media(workspace.run_id),
            metadata={"zgraph_home": str(self.settings.zgraph_home)},
        )
        tool_registry = build_default_tool_registry(context)
        skill_started = time.perf_counter()
        skills = SkillLoader(self._skill_dirs()).load()
        self.logger.info(
            "run_id=%s stage=skills:end elapsed_ms=%.2f count=%s",
            workspace.run_id,
            (time.perf_counter() - skill_started) * 1000,
            len(skills),
        )

        state: dict[str, Any] = {
            "run_id": workspace.run_id,
            "user_input": user_input,
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
        intent_started = time.perf_counter()
        self.logger.info("run_id=%s stage=intent:start", workspace.run_id)
        intent_result = self.intent_workflow.run(state)
        state.update(intent_result.data)
        self.logger.info(
            "run_id=%s stage=intent:end elapsed_ms=%.2f intent=%s source=%s",
            workspace.run_id,
            (time.perf_counter() - intent_started) * 1000,
            (state.get("intent") or {}).get("name"),
            (state.get("hint") or {}).get("source"),
        )

        capability_started = time.perf_counter()
        self.logger.info("run_id=%s stage=capability:start", workspace.run_id)
        compiler = CapabilityCompiler(self.settings, tool_registry, skills)
        capabilities = compiler.compile(state)
        state["capabilities"] = capabilities
        self.logger.info(
            "run_id=%s stage=capability:end elapsed_ms=%.2f skills=%s tools=%s risk=%s strategy=%s",
            workspace.run_id,
            (time.perf_counter() - capability_started) * 1000,
            capabilities.get("selected_skills"),
            capabilities.get("selected_tools"),
            capabilities.get("risk_level"),
            capabilities.get("retrieval_strategy"),
        )

        if self._is_recommendation_request(state):
            self.logger.info("run_id=%s stage=recommendation:start", workspace.run_id)
            return self._run_recommendation(workspace, state).to_dict()

        interrupt: dict[str, Any] | None = None
        if capabilities["risk_level"] in {"medium", "high"}:
            guardian_started = time.perf_counter()
            self.logger.info("run_id=%s stage=guardian:start risk=%s", workspace.run_id, capabilities["risk_level"])
            validation = self.validate_workflow.run(state)
            if validation.status != "completed":
                return RuntimeResult(
                    run_id=workspace.run_id,
                    status="failed",
                    content="Guardian validation failed",
                    hint=state.get("hint", {}),
                    intent=state.get("intent", {}),
                    todo=state.get("todo", []),
                    capabilities=capabilities,
                    error="; ".join(validation.errors),
                ).to_dict()
            risk = self.risk_workflow.run(state)
            capabilities["risk_level"] = risk.data["risk_level"]
            state["risk_level"] = capabilities["risk_level"]
            approval = self.approve_workflow.run(state)
            if approval.status == "interrupted":
                interrupt = approval.data["interrupt"]
                context.interrupts[interrupt["interrupt_id"]] = interrupt
                if not self.settings.auto_approve_interrupts:
                    self._write_audit(workspace, state, interrupt=interrupt)
                    return RuntimeResult(
                        run_id=workspace.run_id,
                        status="interrupted",
                        content="High risk task requires explicit approval.",
                        hint=state.get("hint", {}),
                        intent=state.get("intent", {}),
                        todo=state.get("todo", []),
                        capabilities=capabilities,
                        interrupt=interrupt,
                    ).to_dict()

                interrupt["status"] = "approved"
                interrupt["decision_reason"] = "auto-approved by runtime policy"
                state["interrupt"] = interrupt
                state["auto_approved"] = True
                context.allow_bash = True
            self.logger.info(
                "run_id=%s stage=guardian:end elapsed_ms=%.2f risk=%s interrupt_status=%s",
                workspace.run_id,
                (time.perf_counter() - guardian_started) * 1000,
                capabilities.get("risk_level"),
                (interrupt or {}).get("status"),
            )

        selected_tools = self._selected_tools(capabilities, tool_registry)
        execute_started = time.perf_counter()
        self.logger.info(
            "run_id=%s stage=execute:start tools=%s offline=%s",
            workspace.run_id,
            [tool.name for tool in selected_tools],
            self.settings.offline,
        )
        content = self._execute(user_input, workspace, selected_tools, state, skills, tool_registry)
        if isinstance(content, AgentResult):
            agent_result = content
            content_text = agent_result.content
            reasoning_text = agent_result.reasoning_content
        else:
            agent_result = None
            content_text = content
            reasoning_text = ""
        self.logger.info(
            "run_id=%s stage=execute:end elapsed_ms=%.2f chars=%s reasoning_chars=%s",
            workspace.run_id,
            (time.perf_counter() - execute_started) * 1000,
            len(content_text),
            len(reasoning_text),
        )
        artifacts = [str(path) for path in workspace.artifacts_dir.glob("*") if path.is_file()]
        # Phase 3.4 收尾：不再读 ``outputs_dir``（已是 ``storage_dir`` 别名），直接
        # 走 storage_dir。这样以后把别名砍掉也不会破。
        artifacts.extend(str(path) for path in workspace.storage_dir.glob("*") if path.is_file())

        result = RuntimeResult(
            run_id=workspace.run_id,
            status=str(state.get("_execution_status", "completed")),
            content=content_text,
            hint=state.get("hint", {}),
            intent=state.get("intent", {}),
            todo=state.get("todo", []),
            capabilities=capabilities,
            interrupt=interrupt,
            artifacts=artifacts,
            error=state.get("_execution_error"),
            data=state.get("_execution_data"),
            reasoning_content=reasoning_text,
            media=[m.to_dict() for m in self._consume_media(workspace.run_id)],
        )
        self._save_memory(user_input, result)
        self.logger.info("run_id=%s stage=audit:start", workspace.run_id)
        self._write_audit(workspace, state, result=result.to_dict())
        self.logger.info("run_id=%s stage=audit:end", workspace.run_id)
        self.workspace_manager.cleanup_expired(self.settings.run_ttl_seconds)
        return result.to_dict()

    def recommend_questions(self, *, run_id: str | None = None) -> RuntimeResult:
        """推荐问题"""
        workspace = self.workspace_manager.create_run(run_id)
        state: dict[str, Any] = {
            "run_id": workspace.run_id,
            "user_input": "recommend questions",
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "hint": {
                "summary": "recommend questions from latest memory message",
                "domain": "conversation",
                "task_type": "recommend_questions",
                "keywords": ["recommend", "questions"],
                "slots": {},
                "candidate_workflows": ["recommend_questions"],
                "candidate_tools": [],
                "risk_signals": [],
            },
            "intent": {
                "name": "recommend_questions",
                "confidence": 1.0,
                "difficulty": "easy",
                "risk_hint": "low",
            },
            "todo": [
                {"id": 1, "item": "Load latest saved message"},
                {"id": 2, "item": "Generate structured recommended questions"},
            ],
            "capabilities": {
                "selected_skills": ["question_recommendation"],
                "selected_tools": [],
                "required_tools": [],
                "selected_workflows": ["recommend_questions"],
                "preconditions": ["latest-message-available"],
                "validations": ["structured-recommendation-output"],
                "risk_level": "low",
                "spawn_required": False,
            },
        }
        return self._run_recommendation(workspace, state)

    def validate_workflows(self, *, run_id: str | None = None) -> RuntimeResult:
        """校验工作流"""
        workspace = self.workspace_manager.create_run(run_id)
        context = ToolContext(workspace=workspace, allow_bash=False, metadata={"zgraph_home": str(self.settings.zgraph_home)})
        tool_registry = build_default_tool_registry(context)
        skills = SkillLoader(self._skill_dirs()).load()
        checked: list[dict[str, Any]] = []
        seen_sources: set[str] = set()
        for skill in skills:
            try:
                definition = self.workflow_registry.find_for_skills([skill])
            except Exception as exc:
                checked.append(
                    {
                        "skill": skill.name,
                        "source": "",
                        "valid": False,
                        "errors": [str(exc)],
                    }
                )
                continue
            if definition is None:
                continue
            source = str(definition.source)
            if source in seen_sources:
                continue
            seen_sources.add(source)
            validation = validate_workflow_spec(definition.spec, tool_registry=tool_registry)
            checked.append(
                {
                    "skill": definition.skill.name,
                    "workflow": definition.name,
                    "source": source,
                    "valid": validation.valid,
                    "errors": validation.errors,
                    "required_inputs": [
                        name for name, item in definition.spec.inputs.items() if item.required
                    ],
                }
            )

        payload = {
            "valid": all(item["valid"] for item in checked),
            "count": len(checked),
            "workflows": checked,
        }
        status = "completed" if payload["valid"] else "failed"
        content = json.dumps(payload, ensure_ascii=False, indent=2)
        result = RuntimeResult(
            run_id=workspace.run_id,
            status=status,
            content=content,
            data=payload,
        )
        self._write_audit(workspace, {"run_id": workspace.run_id, "validation": "workflows"}, result=result.to_dict())
        return result

    def resume_interrupted(
        self,
        run_id: str,
        *,
        approve: bool,
        reason: str = "",
    ) -> RuntimeResult:
        """中断续行"""
        workspace = self.workspace_manager.create_run(run_id)
        audit_path = workspace.logs_dir / "audit.json"
        if not audit_path.exists():
            return RuntimeResult(
                run_id=run_id,
                status="failed",
                content=f"No interrupted run found for {run_id}.",
                error="audit log not found",
            )

        audit = json.loads(audit_path.read_text(encoding="utf-8"))
        state = audit.get("state") or {}
        interrupt = audit.get("interrupt") or {}
        if not interrupt or interrupt.get("status") != "pending":
            return RuntimeResult(
                run_id=run_id,
                status="failed",
                content=f"Run {run_id} does not have a pending interrupt.",
                hint=state.get("hint", {}),
                intent=state.get("intent", {}),
                todo=state.get("todo", []),
                capabilities=state.get("capabilities", {}),
                interrupt=interrupt or None,
                error="pending interrupt not found",
            )

        if not approve:
            interrupt["status"] = "refused"
            interrupt["decision_reason"] = reason
            result = RuntimeResult(
                run_id=run_id,
                status="refused",
                content="Interrupted task was refused.",
                hint=state.get("hint", {}),
                intent=state.get("intent", {}),
                todo=state.get("todo", []),
                capabilities=state.get("capabilities", {}),
                interrupt=interrupt,
            )
            self._write_audit(workspace, state, result=result.to_dict(), interrupt=interrupt)
            return result

        interrupt["status"] = "approved"
        interrupt["decision_reason"] = reason
        state["interrupt"] = interrupt
        state["risk_level"] = state.get("capabilities", {}).get("risk_level", state.get("risk_level"))

        context = ToolContext(workspace=workspace, allow_bash=True, metadata={"zgraph_home": str(self.settings.zgraph_home)})
        tool_registry = build_default_tool_registry(context)
        skills = SkillLoader(self._skill_dirs()).load()
        selected_tools = self._selected_tools(state.get("capabilities", {}), tool_registry)
        user_input = str(state.get("user_input", ""))

        content = self._execute(user_input, workspace, selected_tools, state, skills, tool_registry)
        artifacts = [str(path) for path in workspace.artifacts_dir.glob("*") if path.is_file()]
        # Phase 3.4 收尾：直接走 storage_dir
        artifacts.extend(str(path) for path in workspace.storage_dir.glob("*") if path.is_file())
        result = RuntimeResult(
            run_id=run_id,
            status=str(state.get("_execution_status", "completed")),
            content=content,
            hint=state.get("hint", {}),
            intent=state.get("intent", {}),
            todo=state.get("todo", []),
            capabilities=state.get("capabilities", {}),
            interrupt=interrupt,
            artifacts=artifacts,
            error=state.get("_execution_error"),
            data=state.get("_execution_data"),
        )
        self._save_memory(user_input, result)
        self._write_audit(workspace, state, result=result.to_dict(), interrupt=interrupt)
        return result

    def _selected_tools(
        self,
        capabilities: dict[str, Any],
        tool_registry: Any,
    ) -> list[RuntimeTool]:
        """选中工具"""
        selected: list[RuntimeTool] = []
        for name in capabilities.get("selected_tools", []):
            tool = tool_registry.get(name)
            if tool is not None:
                selected.append(tool)
        return selected

    def _is_recommendation_request(self, state: dict[str, Any]) -> bool:
        """判断是否为recommendation请求"""
        intent = state.get("intent") or {}
        hint = state.get("hint") or {}
        return intent.get("name") == "recommend_questions" or hint.get("task_type") == "recommend_questions"

    def _run_recommendation(self, workspace: RunWorkspace, state: dict[str, Any]) -> RuntimeResult:
        """运行recommendation"""
        recommendation = self.recommend_workflow.run(state)
        payload = recommendation.data
        content = json.dumps(payload, ensure_ascii=False)
        result = RuntimeResult(
            run_id=workspace.run_id,
            status="completed",
            content=content,
            hint=state.get("hint", {}),
            intent=state.get("intent", {}),
            todo=state.get("todo", []),
            capabilities=state.get("capabilities", {}),
            artifacts=[],
            data=payload,
        )
        self._write_audit(workspace, state, result=result.to_dict())
        return result

    def _execute(
        self,
        user_input: str,
        workspace: RunWorkspace,
        selected_tools: list[RuntimeTool],
        state: dict[str, Any],
        skills: list[Any],
        tool_registry: Any,
    ) -> AgentResult | str:
        """执行工作流
            参数:
                user_input: 用户输入（str）
                workspace: 工作空间（RunWorkspace）
                selected_tools: selected工具（list[RuntimeTool]）
                state: 状态（dict[str, Any]]）
                skills: 技能（list[Any]）
                tool_registry: 工具注册表（Any）
            返回:
                AgentResult（含 content / reasoning_content）或 str（兼容老路径：workflow / offline）。
            """
        if self._should_use_temporary_workflow(state):
            return self._execute_temporary_workflow(
                user_input,
                workspace,
                selected_tools,
                state,
                skills,
                tool_registry,
            )

        if self.settings.offline or not self.settings.api_key:
            self.logger.info("run_id=%s stage=execute.offline:start", workspace.run_id)
            offline_text = self._offline_execute(user_input, workspace, state)
            return AgentResult(content=str(offline_text))
        try:
            self.logger.info("run_id=%s stage=agent:start", workspace.run_id)
            started = time.perf_counter()
            return self.agent_manager.run(
                workspace.run_id,
                user_input,
                selected_tools,
                system_prompt=self._system_prompt_with_skills(state, skills),
                conversation_path=workspace.logs_dir / "conversation.json",
            )
        except Exception as exc:
            self.logger.warning("provider execution failed, falling back offline: %s", exc)
            offline_text = self._offline_execute(user_input, workspace, state, error=str(exc))
            return AgentResult(content=str(offline_text))
        finally:
            self.logger.info(
                "run_id=%s stage=agent:end elapsed_ms=%.2f",
                workspace.run_id,
                (time.perf_counter() - started) * 1000 if "started" in locals() else 0.0,
            )

    def _should_use_temporary_workflow(self, state: dict[str, Any]) -> bool:
        """判断是否应该使用temporary工作流。
            参数:
                state: 状态（dict[str, Any]）
            返回:
                返回类型为 bool 的结果
            """
        workflows = set((state.get("capabilities") or {}).get("selected_workflows") or [])
        return "temporary_workflow" in workflows

    def _execute_temporary_workflow(
        self,
        user_input: str,
        workspace: RunWorkspace,
        selected_tools: list[RuntimeTool],
        state: dict[str, Any],
        skills: list[Any],
        tool_registry: Any,
    ) -> str:
        """执行temporary工作流。
            参数:
                user_input: 用户输入（str）
                workspace: 工作空间（RunWorkspace）
                selected_tools: selected工具（list[RuntimeTool]）
                state: 状态（dict[str, Any]）
                skills: 技能（list[Any]）
                tool_registry: 工具注册表（Any）
            返回:
                返回类型为 str 的结果
            """
        selected_skills = self._selected_skill_objects(state, skills)
        configured = self.workflow_registry.find_for_skills(selected_skills)
        if configured is not None:
            return self._execute_configured_workflow(
                user_input,
                workspace,
                state,
                tool_registry,
                configured,
            )

        planner_available = not self.settings.offline and bool(self.settings.api_key)
        self.logger.info(
            "run_id=%s stage=workflow.plan:start skills=%s tools=%s",
            workspace.run_id,
            [skill.name for skill in selected_skills],
            [tool.name for tool in selected_tools],
        )
        try:
            plan = TemporaryWorkflowPlanner(self.settings).plan(
                user_input=user_input,
                state=state,
                skills=selected_skills,
                tools=selected_tools,
            )
        except Exception as exc:
            return self._workflow_failed(
                workspace,
                state,
                "workflow planning failed",
                errors=[str(exc)],
            )

        workflow_yaml = plan.workflow_yaml
        workflow_path = workspace.drafts_dir / "workflow.yaml"
        workflow_path.write_text(workflow_yaml, encoding="utf-8")
        self.logger.info("run_id=%s stage=workflow.plan:end path=%s", workspace.run_id, workflow_path)

        try:
            spec = self.workflow_builder.spec_from_text(workflow_yaml, source=str(workflow_path))
        except Exception as exc:
            return self._workflow_failed(
                workspace,
                state,
                "workflow yaml parse failed",
                errors=[str(exc)],
                workflow_plan={"path": str(workflow_path), "notes": plan.notes},
            )

        validation = validate_workflow_spec(spec, tool_registry=tool_registry)
        if not validation.valid:
            return self._workflow_failed(
                workspace,
                state,
                "workflow validation failed",
                errors=validation.errors,
                workflow_plan={"path": str(workflow_path), "notes": plan.notes},
            )

        review_payload: dict[str, Any] = {}
        self.logger.info("run_id=%s stage=workflow.review:start", workspace.run_id)
        try:
            review = TemporaryWorkflowReviewer(self.settings).review(
                user_input=user_input,
                workflow_yaml=workflow_yaml,
                skills=selected_skills,
                tools=selected_tools,
            )
            review_payload = review.model_dump()
        except Exception as exc:
            return self._workflow_failed(
                workspace,
                state,
                "workflow review failed",
                errors=[str(exc)],
                workflow_plan={"path": str(workflow_path), "notes": plan.notes},
            )

        if review_payload.get("corrected_workflow_yaml"):
            workflow_yaml = str(review_payload["corrected_workflow_yaml"])
            corrected_path = workspace.drafts_dir / "workflow.reviewed.yaml"
            corrected_path.write_text(workflow_yaml, encoding="utf-8")
            workflow_path = corrected_path
            try:
                spec = self.workflow_builder.spec_from_text(workflow_yaml, source=str(corrected_path))
            except Exception as exc:
                return self._workflow_failed(
                    workspace,
                    state,
                    "reviewed workflow yaml parse failed",
                    errors=[str(exc)],
                    workflow_plan={"path": str(workflow_path), "notes": plan.notes},
                    workflow_review=review_payload,
                )
            validation = validate_workflow_spec(spec, tool_registry=tool_registry)
            if not validation.valid:
                return self._workflow_failed(
                    workspace,
                    state,
                    "reviewed workflow validation failed",
                    errors=validation.errors,
                    workflow_plan={"path": str(workflow_path), "notes": plan.notes},
                    workflow_review=review_payload,
                )

        if not review_payload.get("approved", False):
            return self._workflow_failed(
                workspace,
                state,
                "workflow review rejected the plan",
                errors=[str(issue) for issue in review_payload.get("issues", [])],
                workflow_plan={"path": str(workflow_path), "notes": plan.notes},
                workflow_review=review_payload,
            )
        self.logger.info("run_id=%s stage=workflow.review:end approved=true", workspace.run_id)

        execution_state = dict(state)
        execution_state["workflow_planner_available"] = planner_available
        result = WorkflowExecutor(tool_registry).run(
            spec,
            initial_variables={"workflow_planner_available": planner_available},
            state=execution_state,
        )
        result_path = workspace.logs_dir / "workflow-result.json"
        result_path.write_text(json.dumps(result.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")

        payload = {
            "workflow": result.to_dict(),
            "workflow_plan": {"path": str(workflow_path), "notes": plan.notes},
            "workflow_review": review_payload,
        }
        state["_execution_status"] = result.status
        state["_execution_data"] = payload
        if not result.ok:
            state["_execution_error"] = "; ".join(result.errors)
            return "Temporary workflow failed.\n" + result.to_text()

        return "Temporary workflow completed.\n" + result.to_text()

    def _execute_configured_workflow(
        self,
        user_input: str,
        workspace: RunWorkspace,
        state: dict[str, Any],
        tool_registry: Any,
        definition: WorkflowDefinition,
    ) -> str:
        """执行configured工作流。
            参数:
                user_input: 用户输入（str）
                workspace: 工作空间（RunWorkspace）
                state: 状态（dict[str, Any]）
                tool_registry: 工具注册表（Any）
                definition: definition（WorkflowDefinition）
            返回:
                返回类型为 str 的结果
            """
        self.logger.info(
            "run_id=%s stage=workflow.configured:start workflow=%s source=%s",
            workspace.run_id,
            definition.name,
            definition.source,
        )
        workflow_path = workspace.drafts_dir / "workflow.yaml"
        workflow_text = definition.source.read_text(encoding="utf-8")
        workflow_path.write_text(workflow_text, encoding="utf-8")

        validation = validate_workflow_spec(definition.spec, tool_registry=tool_registry)
        workflow_plan = {
            "path": str(workflow_path),
            "source": str(definition.source),
            "mode": "configured",
            "skill": definition.skill.name,
        }
        if not validation.valid:
            return self._workflow_failed(
                workspace,
                state,
                "configured workflow validation failed",
                errors=validation.errors,
                workflow_plan=workflow_plan,
            )

        slots = self.workflow_slot_resolver.resolve(definition.spec, user_input=user_input, state=state)
        if not slots.ok:
            errors = list(slots.errors)
            if slots.missing:
                errors.append("missing required workflow inputs: " + ", ".join(slots.missing))
            return self._workflow_failed(
                workspace,
                state,
                "configured workflow input validation failed",
                errors=errors,
                workflow_plan={
                    **workflow_plan,
                    "slots": slots.slots,
                    "slot_source": slots.source,
                    "auto_fixed": slots.auto_fixed,
                    "fixes": slots.fixes,
                },
            )

        state["slots"] = slots.slots
        execution_state = dict(state)
        execution_state["slots"] = slots.slots
        result = WorkflowExecutor(tool_registry).run(
            definition.spec,
            initial_variables={
                "inputs": slots.slots,
                "workflow_planner_available": False,
            },
            state=execution_state,
        )
        result_path = workspace.logs_dir / "workflow-result.json"
        result_path.write_text(json.dumps(result.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")

        payload = {
            "workflow": result.to_dict(),
            "workflow_plan": {
                **workflow_plan,
                "slots": slots.slots,
                "slot_source": slots.source,
                "auto_fixed": slots.auto_fixed,
                "fixes": slots.fixes,
            },
            "workflow_review": {"approved": True, "issues": ["configured workflow; LLM review skipped"]},
        }
        state["_execution_status"] = result.status
        state["_execution_data"] = payload
        if not result.ok:
            state["_execution_error"] = "; ".join(result.errors)
            return "Configured workflow failed.\n" + result.to_text()

        self.logger.info("run_id=%s stage=workflow.configured:end status=completed", workspace.run_id)
        return "Configured workflow completed.\n" + result.to_text()

    def _selected_skill_objects(self, state: dict[str, Any], skills: list[Any]) -> list[Any]:
        """选择技能"""
        selected = set((state.get("capabilities") or {}).get("selected_skills") or [])
        return [skill for skill in skills if getattr(skill, "name", "") in selected]

    def _workflow_failed(
        self,
        workspace: RunWorkspace,
        state: dict[str, Any],
        message: str,
        *,
        errors: list[str],
        workflow_plan: dict[str, Any] | None = None,
        workflow_review: dict[str, Any] | None = None,
    ) -> str:
        """工作流failed"""
        payload = {
            "workflow": {"status": "failed", "errors": errors},
            "workflow_plan": workflow_plan or {},
            "workflow_review": workflow_review or {},
        }
        (workspace.logs_dir / "workflow-result.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        state["_execution_status"] = "failed"
        state["_execution_error"] = "; ".join(errors)
        state["_execution_data"] = payload
        return f"{message}: {state['_execution_error']}"

    def _system_prompt_with_skills(self, state: dict[str, Any], skills: list[Any]) -> str:
        """技能注入"""
        selected = set((state.get("capabilities") or {}).get("selected_skills") or [])
        if not selected:
            return self.settings.system_prompt

        chunks: list[str] = []
        remaining = self.settings.skill_context_char_limit
        for skill in skills:
            if skill.name not in selected or remaining <= 0:
                continue
            text = (
                f"## Skill: {skill.name}\n"
                f"Description: {skill.description}\n"
                f"Required tools: {', '.join(skill.required_tools)}\n"
                f"{skill.content}\n"
            )
            clipped = text[:remaining]
            chunks.append(clipped)
            remaining -= len(clipped)

        if not chunks:
            return self.settings.system_prompt
        return (
            f"{self.settings.system_prompt}\n\n"
            "Use the following selected runtime skills when relevant. "
            "They are task instructions, not user-visible text.\n\n"
            + "\n\n".join(chunks)
        )

    def _offline_execute(
        self,
        user_input: str,
        workspace: RunWorkspace,
        state: dict[str, Any],
        *,
        error: str | None = None,
    ) -> str:
        """offline执行
            参数:
                user_input: 用户输入（str）
                workspace: 工作空间（RunWorkspace）
                state: 状态（dict[str, Any]）
            返回:
                返回类型为 str 的结果
            """
        payload = {
            "message": "ZGraph runtime completed in offline mode.",
            "input": user_input,
            "hint": state.get("hint", {}),
            "intent": state.get("intent", {}),
            "capabilities": state.get("capabilities", {}),
        }
        if error:
            payload["provider_error"] = error
        # Phase 3.4 收尾：写入 storage_dir（不再依赖 outputs_dir 别名）
        target = workspace.storage_dir / "runtime-result.json"
        target.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        if state.get("intent", {}).get("name") == "chat":
            return "ZGraph runtime is ready. Provider execution is offline, so I returned a local runtime response."
        return json.dumps(payload, ensure_ascii=False, indent=2)

    def _save_memory(self, user_input: str, result: RuntimeResult) -> None:
        """保存记忆。
            参数:
                user_input: 用户输入（str）
                result: 结果（RuntimeResult）
            """
        messages = [
            {"role": "user", "content": user_input},
            {"role": "assistant", "content": result.content},
        ]
        summary = self.memory_compressor.compress(
            messages,
            max_chars=1600,
        )
        self.memory_saver.save(
            {
                "run_id": result.run_id,
                "created_at": time.time(),
                "summary": summary,
                "messages": messages,
                "latest_message": result.content,
                "status": result.status,
            }
        )

    def _write_audit(
        self,
        workspace: RunWorkspace,
        state: dict[str, Any],
        *,
        result: dict[str, Any] | None = None,
        interrupt: dict[str, Any] | None = None,
    ) -> None:
        """写入audit。
            参数:
                workspace: 工作空间（RunWorkspace）
                state: 状态（dict[str, Any]）
            """
        payload = {
            "state": state,
            "result": result,
            "interrupt": interrupt,
            "created_at": time.time(),
        }
        (workspace.logs_dir / "audit.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
