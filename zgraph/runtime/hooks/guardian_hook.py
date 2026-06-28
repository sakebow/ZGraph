"""Phase 4：Guardian as Hook。

设计：
- GuardianHook 把现有 validate / risk / approve 三段式逻辑搬到 hook 层
- 监听 Final 事件：检查 capability 的 risk_level 和实际 tool calls
- 风险升级或高风险时，emit Interrupt 事件；否则透传 Final
- Runtime 默认注册 GuardianHook（可被覆盖）
"""

from __future__ import annotations

import logging
import uuid
from typing import TYPE_CHECKING, Any

from zgraph.runtime.events import Final, Interrupt, RuntimeEvent, ToolCallStart
from zgraph.workflow.guardian.approve import ApproveWorkflow
from zgraph.workflow.guardian.risk import RANK, RiskWorkflow
from zgraph.workflow.guardian.validate import ValidateWorkflow

if TYPE_CHECKING:
    from zgraph.runtime.hooks import RunContext

logger = logging.getLogger("zgraph.guardian_hook")


_HIGH_RISK_TOOLS = {"bash", "delete", "http", "adapter.call"}


class GuardianHook:
    """把 Guardian 三段式（validate / risk / approve）搬到 hook 层。

    实现说明：
    - 监听 ToolCallStart 累积 tool call 列表（用于事后检查）
    - 监听 Final 事件：跑 validate / risk / approve，如果 interrupted 就
      yield Interrupt 事件，再 yield 一个新的 Final（status=interrupted）
    - 其它事件透传

    兼容性：
    - 现有 Runtime._run_unprotected 里仍然直接调 validate_workflow / risk_workflow /
      approve_workflow（在 astream() 内），保留向后兼容；本 hook 主要作用于
      astream() 的 Final 阶段和钩子链路上的工具调用。
    """

    HIGH_RISK_TOOLS = _HIGH_RISK_TOOLS

    def __init__(self) -> None:
        self._validate = ValidateWorkflow()
        self._risk = RiskWorkflow()
        self._approve = ApproveWorkflow()
        # 累积每个 run 期间出现的 tool calls
        self._tool_calls: dict[str, list[str]] = {}

    async def __call__(self, event: RuntimeEvent, ctx: Any) -> RuntimeEvent | None:
        run_id = ctx.run_id
        # 累积 tool 调用名（用于事后风险评估）
        if isinstance(event, ToolCallStart):
            self._tool_calls.setdefault(run_id, []).append(event.tool_name)
            return event

        if not isinstance(event, Final):
            return event

        # 在 Final 阶段做 Guardian 三段式
        rt = event.runtime_result
        if rt is None:
            return event

        # 1. validate：检查必要字段
        state = {
            "hint": getattr(rt, "hint", {}),
            "intent": getattr(rt, "intent", {}),
            "todo": getattr(rt, "todo", []),
            "capabilities": getattr(rt, "capabilities", {}),
        }
        validation = self._validate.run(state)
        if validation.status != "completed":
            # validation 失败：发 Interrupt + 新 Final（status=interrupted）
            reason = "; ".join(validation.errors) or "validation failed"
            interrupt_id = uuid.uuid4().hex
            return self._interrupt_final(
                run_id=run_id,
                interrupt_id=interrupt_id,
                reason=reason,
                original=event,
                ctx=ctx,
            )

        # 2. risk：合并 capability 的 risk + 实际 tool calls 升级
        actual_tools = set(self._tool_calls.get(run_id, []))
        risk_level = self._compute_risk(state, actual_tools)

        # 3. approve：high 风险 → interrupt
        if risk_level == "high":
            interrupt_id = uuid.uuid4().hex
            return self._interrupt_final(
                run_id=run_id,
                interrupt_id=interrupt_id,
                reason=f"high risk tool detected: {actual_tools & _HIGH_RISK_TOOLS}",
                original=event,
                ctx=ctx,
            )

        # 通过：把更新后的 risk_level 写回 capabilities，透传 Final
        caps = dict(state.get("capabilities") or {})
        caps["risk_level"] = risk_level
        new_rt_dict = rt.to_dict()
        new_rt_dict["capabilities"] = caps
        from zgraph.runtime import RuntimeResult

        new_rt = RuntimeResult(
            run_id=rt.run_id,
            status=rt.status,
            content=rt.content,
            **{
                k: new_rt_dict.get(k)
                for k in (
                    "hint", "intent", "todo", "capabilities",
                    "interrupt", "artifacts", "error", "data",
                    "reasoning_content", "media", "interrupt_token",
                )
            },
        )
        return Final(
            run_id=event.run_id,
            status=event.status,
            finish_reason=event.finish_reason,
            runtime_result=new_rt,
        )

    def _compute_risk(self, state: dict[str, Any], actual_tools: set[str]) -> str:
        """合并 capability 风险 + 实际 tool 调用。"""
        caps = state.get("capabilities") or {}
        risk = str(caps.get("risk_level") or state.get("intent", {}).get("risk_hint") or "low")
        if actual_tools & _HIGH_RISK_TOOLS:
            risk = "high"
        elif actual_tools & {"write", "update", "settodolist", "spawn"} and RANK.get(risk, 0) < 1:
            risk = "medium"
        return risk

    def _interrupt_final(
        self,
        *,
        run_id: str,
        interrupt_id: str,
        reason: str,
        original: Final,
        ctx: Any,
    ) -> Final:
        """构造一个 status=interrupted 的新 Final，附 Interrupt 信息。"""
        # 返回一个特殊标记：runtime 需要把这次的 Final 转成 Interrupt + 终止流
        # 这里用 ctx.metadata 传递 Interrupt 给 runtime（因为 RuntimeHook 不能 yield）
        interrupt = Interrupt(
            run_id=run_id,
            tool_call_id=interrupt_id,
            tool_name="guardian",
            reason=reason,
            interrupt_token=interrupt_id,
        )
        ctx.metadata["guardian_interrupt"] = interrupt.to_dict() if hasattr(interrupt, "to_dict") else {
            "run_id": interrupt.run_id,
            "tool_call_id": interrupt.tool_call_id,
            "tool_name": interrupt.tool_name,
            "reason": interrupt.reason,
            "interrupt_token": interrupt.interrupt_token,
        }
        # 替换 Final 为 interrupted 状态
        rt = original.runtime_result
        if rt is not None:
            from zgraph.runtime import RuntimeResult

            new_rt = RuntimeResult(
                run_id=rt.run_id,
                status="interrupted",
                content=rt.content,
                **{k: getattr(rt, k) for k in (
                    "hint", "intent", "todo", "capabilities",
                    "artifacts", "error", "data",
                    "reasoning_content", "media",
                )},
                interrupt={"interrupt_id": interrupt_id, "reason": reason},
                interrupt_token=interrupt_id,
            )
            return Final(
                run_id=run_id,
                status="interrupted",
                finish_reason="interrupt",
                runtime_result=new_rt,
            )
        return original
