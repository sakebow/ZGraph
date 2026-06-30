"""内置 RuntimeHook 实现（Phase 2.3）。

三类内置钩子：
- AuditHook：在 Final 事件触发时把 RuntimeResult 写入 audit.json
- MetricsHook：累计 token 数 / 事件数到 ctx.metadata
- PIIFilterHook：在 ContentDelta 事件上做 PII mask（邮箱 / 手机号 / 身份证）
"""

from __future__ import annotations

import json
import logging
import re
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

from zgraph.runtime.events import ContentDelta, Final, RuntimeEvent

if TYPE_CHECKING:
    from zgraph.runtime.hooks import RunContext


logger = logging.getLogger("zgraph.hooks")


class AuditHook:
    """Final 事件触发时把 RuntimeResult 写到 audit.json。

    默认路径：``$ZGRAPH_HOME/runs/{run_id}/logs/audit.json``。如果路径已存在则
    追加单条记录（NDJSON 风格），否则新建。
    """

    def __init__(self, *, path: Path | None = None) -> None:
        self._explicit_path = path

    async def __call__(self, event: RuntimeEvent, ctx: Any) -> RuntimeEvent | None:
        if not isinstance(event, Final):
            return event
        rt = event.runtime_result
        if rt is None:
            return event
        target = self._resolve_path(ctx)
        # 兼容 rt 是 RuntimeResult 对象 / dict（测试或自定义调用方可能给 dict）
        if isinstance(rt, dict):
            status = rt.get("status", "")
            content = rt.get("content", "")
            reasoning = rt.get("reasoning_content", "")
            interrupt = rt.get("interrupt")
            error = rt.get("error")
            media = rt.get("media") or []
        else:
            status = getattr(rt, "status", "") or ""
            content = getattr(rt, "content", "") or ""
            reasoning = getattr(rt, "reasoning_content", "") or ""
            interrupt = getattr(rt, "interrupt", None)
            error = getattr(rt, "error", None)
            media = getattr(rt, "media", None) or []
        # Phase 3.7：把每条 media 的元数据（block_id / modality / mime / size /
        # expires_at / url）单独记进 audit，便于事后回溯媒体生命周期。
        media_records: list[dict[str, Any]] = []
        for item in media:
            if not isinstance(item, dict):
                continue
            media_records.append(
                {
                    "block_id": item.get("block_id"),
                    "modality": item.get("modality"),
                    "mime": item.get("mime"),
                    "size_bytes": item.get("size_bytes"),
                    "expires_at": item.get("expires_at"),
                    "url": item.get("url"),
                }
            )
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            # Phase 5.1：把完整 interrupt dict 一起写进 audit.json。
            # 之前 `interrupt` 字段是 bool，`resume_interrupted()` 读时按 dict 处理会
            # AttributeError。`interrupt_payload` 是可选字段，仅在确有 interrupt 时写。
            interrupt_payload: dict[str, Any] | None = None
            if interrupt and isinstance(interrupt, dict):
                interrupt_payload = dict(interrupt)
            # Phase 5.1：把 ``ctx.state`` 完整写进 entry，便于 ``resume_interrupted``
            # 在 AuditHook 写出的 NDJSON 上恢复状态。AuditHook 不需要 state 时（ctx.state
            # 为 None），不写这个字段保持向后兼容。
            entry: dict[str, Any] = {
                "ts": time.time(),
                "run_id": event.run_id,
                "status": status,
                "content_len": len(content or ""),
                "reasoning_len": len(reasoning or ""),
                "interrupt": bool(interrupt),
                "interrupt_payload": interrupt_payload,
                "error": error,
                "media_count": len(media_records),
                "media": media_records,
                "metadata_keys": list(ctx.metadata.keys()) if ctx.metadata else [],
            }
            # Phase 5.8：把 MetricsHook 累计的 metrics 字典也写进 entry。
            # 之前 metrics 只在 ``ctx.metadata["metrics"]`` 里，没有任何下游消费者
            # 读它——只有 ``metadata_keys`` 列出 key，但 audit.json 里看不到值。
            # 真实运行数据被默默丢掉，无法做后续性能/用量分析。
            metrics_payload = (ctx.metadata or {}).get("metrics") if ctx.metadata else None
            if isinstance(metrics_payload, dict):
                # 不复制引用，防止 AuditHook 内部修改污染 caller
                entry["metrics"] = dict(metrics_payload)
            if getattr(ctx, "state", None):
                entry["state"] = dict(ctx.state)
            with target.open("a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except Exception as exc:
            logger.warning("AuditHook failed to write %s: %s", target, exc)
        return event

    def _resolve_path(self, ctx: Any) -> Path:
        if self._explicit_path is not None:
            return self._explicit_path
        # 默认：zgraph_home/runs/{run_id}/logs/audit.json
        return ctx.settings.zgraph_home / "runs" / ctx.run_id / "logs" / "audit.json"


class MetricsHook:
    """把事件计数、文本长度、reasoning 长度累计到 ctx.metadata。

    在 Final 事件上把指标汇总写到 metrics 字典，供下游 hook / 持久化层使用。
    """

    def __init__(self) -> None:
        pass

    async def __call__(self, event: RuntimeEvent, ctx: Any) -> RuntimeEvent | None:
        m = ctx.metadata.setdefault("metrics", {
            "content_delta_count": 0,
            "content_chars": 0,
            "reasoning_delta_count": 0,
            "reasoning_chars": 0,
            "tool_call_count": 0,
            "interrupt_count": 0,
            "media_count": 0,
        })
        if isinstance(event, ContentDelta):
            m["content_delta_count"] += 1
            m["content_chars"] += len(event.text)
        elif event.__class__.__name__ == "ReasoningDelta":
            m["reasoning_delta_count"] += 1
            m["reasoning_chars"] += len(event.text)
        elif event.__class__.__name__ in ("ToolCallStart", "ToolCallArgs", "ToolCallEnd"):
            m["tool_call_count"] += 1
        elif event.__class__.__name__ == "Interrupt":
            m["interrupt_count"] += 1
        elif event.__class__.__name__ == "MediaReady":
            m["media_count"] += 1
        return event


# 简化版 PII 检测：邮箱 / 中国大陆手机号 / 18 位身份证号
_PII_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("email", re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}")),
    ("cn_phone", re.compile(r"\b1[3-9]\d{9}\b")),
    ("cn_id", re.compile(r"\b\d{17}[\dXx]\b")),
)


class PIIFilterHook:
    """在 ContentDelta 上做 PII mask。其它事件透传。

    实现：正则替换。mask 形如 ``[EMAIL]`` / ``[CN_PHONE]`` / ``[CN_ID]``。
    不会影响 ReasoningDelta / ToolCall 等敏感度低的事件。
    """

    def __init__(self, *, enabled: bool = True) -> None:
        self._enabled = enabled

    async def __call__(self, event: RuntimeEvent, ctx: Any) -> RuntimeEvent | None:
        if not self._enabled or not isinstance(event, ContentDelta):
            return event
        masked = event.text
        for label, pat in _PII_PATTERNS:
            masked = pat.sub(f"[{label.upper()}]", masked)
        if masked == event.text:
            return event
        return ContentDelta(text=masked)
