from __future__ import annotations

import argparse
import json
import logging
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from zgraph.config import Settings
from zgraph.layer.input import CompletionsInputLayer
from zgraph.layer.output import CompletionsGenerateOutputLayer, CompletionsStreamOutputLayer
from zgraph.runtime import ZGraphRuntime


def _json_response(handler: BaseHTTPRequestHandler, status: int, payload: dict[str, Any]) -> None:
    """JSON响应"""
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.send_header("Access-Control-Allow-Origin", "*")
    handler.end_headers()
    handler.wfile.write(body)


def _parse_last_event_id(header_value: str | None) -> int:
    """解析 Last-Event-ID header，返回要跳过的 chunk 序号。

    SSE id 格式：``chatcmpl-<uuid>-<idx>``。
    - 没有 header / 解析失败 → 返回 0（不跳过）
    - 解析成功 → 返回 idx（要跳过前 idx 个 chunk）

    注意：当前实现是"本连接内续传"——只跳过序号，不做跨 completion_id 的
    replay buffer。如果客户端断开太久、buffer 已被回收，会重新走完整流。
    """
    if not header_value:
        return 0
    # 最后一个 "-" 后面是序号
    head, _, tail = header_value.rpartition("-")
    if not head or not tail.isdigit():
        return 0
    idx = int(tail)
    return idx if idx > 0 else 0


class ZGraphHttpHandler(BaseHTTPRequestHandler):
    """zgraph-HTTP处理器。继承自 BaseHTTPRequestHandler。"""
    runtime: ZGraphRuntime
    settings: Settings

    def log_message(self, fmt: str, *args: Any) -> None:
        """记录消息"""
        return

    def do_OPTIONS(self) -> None:
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "content-type, authorization")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.end_headers()

    def do_GET(self) -> None:
        if self.path in {"/", "/health", "/v1/health"}:
            _json_response(self, 200, {"status": "ok", "service": "zgraph"})
            return
        # Phase 3.6：静态文件路由 GET /files/{run_id}/{name}
        if self.path.startswith("/files/"):
            self._serve_static_file()
            return
        _json_response(self, 404, {"error": "not found"})

    def _serve_static_file(self) -> None:
        """GET /files/{run_id}/{name}：从 media_store 取文件并返回。"""
        rel = self.path[len("/files/"):]
        # 安全：禁止 ../
        if ".." in rel.split("/"):
            _json_response(self, 400, {"error": "invalid path"})
            return
        # media_store URL 的形式是 {base_url}/files/{run_id}/{name}
        # 这里 self.path 已经是 /files/{run_id}/{name}，可以直接拼成完整 URL
        base_url = f"http://{self.settings.host}:{self.settings.port}"
        full_url = f"{base_url}{self.path}"
        result = self.runtime.media_store.open(full_url)
        if result is None:
            _json_response(self, 404, {"error": "file not found"})
            return
        try:
            data, mime = result
            self.send_response(200)
            self.send_header("Content-Type", mime)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "public, max-age=3600")
            self.end_headers()
            self.wfile.write(data)
        except Exception as exc:
            # 响应头已发，无法再回 500；只能记日志
            logging.getLogger("zgraph.server").error(
                "failed to serve %s: %s", self.path, exc
            )

    def do_POST(self) -> None:
        if self.path == "/v1/recommendations":
            result = self.runtime.recommend_questions()
            _json_response(self, 200, result.data or {"data": []})
            return

        if self.path != "/v1/chat/completions":
            _json_response(self, 404, {"error": "not found"})
            return
        length = int(self.headers.get("Content-Length", "0") or "0")
        try:
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
        except json.JSONDecodeError:
            _json_response(self, 400, {"error": "invalid json"})
            return

        if self.settings.whitelist:
            app_id = str(payload.get("app_id") or payload.get("user") or "")
            if app_id not in self.settings.whitelist:
                _json_response(self, 403, {"error": "app_id is not allowed"})
                return

        try:
            system_hint = self.runtime.build_examples_hint()
            parsed = CompletionsInputLayer().handle(payload, system_hint=system_hint)
        except Exception as exc:
            _json_response(self, 500, {"error": str(exc)})
            return

        model = str(payload.get("model") or self.settings.model_name)
        if parsed["stream"]:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "keep-alive")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            # P1.4：astream 异步流式；每次请求独立 asyncio loop，
            # 避免 BaseHTTPRequestHandler 同步模型与 aiter() 冲突
            import asyncio
            from zgraph.layer.output import CompletionsAsyncStreamOutputLayer

            # P3.8：Last-Event-ID 断点续传。
            # 客户端断线重连时把上次收到的 SSE id 放 Last-Event-ID，
            # server 从下一个 chunk 开始发。格式 ``chatcmpl-<uuid>-<idx>``，
            # 当前实现简单：只跳过序号，不做跨 completion_id 重放。
            skip_until_chunk_idx = _parse_last_event_id(
                self.headers.get("Last-Event-ID")
            )

            async def _drain():
                events = self.runtime.astream(parsed["prompt"])
                chunk_idx = 0
                async for chunk in CompletionsAsyncStreamOutputLayer().astream(
                    events, model=model
                ):
                    chunk_idx += 1
                    if chunk_idx <= skip_until_chunk_idx:
                        # 已发送过，跳过
                        continue
                    self.wfile.write(chunk)
                    self.wfile.flush()

            try:
                asyncio.run(_drain())
            except Exception as exc:
                # 响应头已发，无法再回 500；只能记日志
                logging.getLogger("zgraph.server").error(
                    "stream failed for prompt=%r: %s", parsed["prompt"], exc
                )
            return

        # 非 stream 路径：唯一一次 agent 调用
        try:
            result = self.runtime.run(parsed["prompt"])
        except Exception as exc:
            _json_response(self, 500, {"error": str(exc)})
            return

        body = CompletionsGenerateOutputLayer().handle({"content": result.content, "model": model})
        body["zgraph"] = result.to_dict()
        _json_response(self, 200, body)


def serve(settings: Settings) -> int:
    """启动fastapi模式
        参数:
            settings: 设置（Settings）
        返回:
            返回类型为 int 的结果
        """
    runtime = ZGraphRuntime(settings)
    ZGraphHttpHandler.runtime = runtime
    ZGraphHttpHandler.settings = settings
    # Phase 3.7：后台媒体清理循环
    from zgraph.runtime.cleanup_loop import MediaCleanupLoop

    cleanup_loop = MediaCleanupLoop(settings.media_cleanup_interval_seconds)
    server: ThreadingHTTPServer | None = None
    try:
        cleanup_loop.start(runtime)
        server = ThreadingHTTPServer((settings.host, settings.port), ZGraphHttpHandler)
        print(f"zgraph serving on http://{settings.host}:{settings.port}")
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            print("\nzgraph stopped")
    finally:
        cleanup_loop.stop()
        if server is not None:
            server.server_close()
    return 0


def run_cli(args: argparse.Namespace, settings: Settings) -> int:
    """运行cli
        参数:
            args: 位置参数
            settings: 设置（Settings）
        返回:
            返回类型为 int 的结果
        """
    if args.offline:
        settings.offline = True
    if args.auto_approve:
        settings.auto_approve_interrupts = True
    runtime = ZGraphRuntime(settings)

    command = args.text[0].lower() if args.text else ""
    if command in {"resume", "approve"}:
        if len(args.text) < 2:
            _print_command_usage("resume", "Approve and continue a pending interrupted run.")
            return 2
        result = runtime.resume_interrupted(args.text[1], approve=True, reason="cli approval")
        _print_cli_result(result, args.json)
        return 0 if result.status == "completed" else 1
    if command in {"refuse", "reject"}:
        if len(args.text) < 2:
            _print_command_usage("refuse", "Refuse a pending interrupted run.")
            return 2
        result = runtime.resume_interrupted(args.text[1], approve=False, reason="cli refusal")
        _print_cli_result(result, args.json)
        return 0 if result.status == "refused" else 1
    if command in {"recommend", "recommendations", "recommend-questions"}:
        result = runtime.recommend_questions()
        _print_cli_result(result, args.json, data_only=True)
        return 0 if result.status == "completed" else 1
    if command in {"validate-workflows", "validate-workflow", "workflow-validate"}:
        result = runtime.validate_workflows()
        _print_cli_result(result, args.json)
        return 0 if result.status == "completed" else 1

    prompt = args.prompt
    if not prompt and args.text:
        prompt = " ".join(args.text)
    # 与 HTTP handler 对齐：把 examples hint 作为系统提示拼到 prompt 前，
    # 让 LLM 看到本地可用资源路径，从而能调 media_input。
    examples_hint = runtime.build_examples_hint()
    def _with_hint(user_prompt: str) -> str:
        if not user_prompt:
            return user_prompt
        if examples_hint:
            return f"system: {examples_hint}\nuser: {user_prompt}"
        return user_prompt
    if not prompt:
        print("Enter a prompt. Press Ctrl+C to exit.")
        pending = None
        while True:
            try:
                prompt = input("> ").strip()
            except (EOFError, KeyboardInterrupt):
                print()
                return 0
            if not prompt:
                continue

            decision = _approval_decision(prompt)
            if pending is not None:
                if decision is None:
                    print(
                        f"Run {pending.run_id} is waiting for approval. "
                        "Type yes to continue or no to refuse."
                    )
                    continue
                result = runtime.resume_interrupted(
                    pending.run_id,
                    approve=decision,
                    reason=f"interactive cli: {prompt}",
                )
                pending = None
            else:
                result = runtime.run(_with_hint(prompt))

            print(result.content)
            if result.status == "interrupted":
                pending = result
                interrupt_id = (result.interrupt or {}).get("interrupt_id", "")
                print(
                    f"Pending approval for run {result.run_id}"
                    + (f" interrupt {interrupt_id}" if interrupt_id else "")
                    + ". Type yes to continue or no to refuse."
                )
        return 0

    result = runtime.run(_with_hint(prompt))
    _print_cli_result(result, args.json)
    if result.status == "interrupted":
        _print_resume_hint(result.run_id)
    return 0 if result.status in {"completed", "interrupted"} else 1


def build_parser() -> argparse.ArgumentParser:
    """构建parser
        返回:
            返回类型为 argparse.ArgumentParser 的结果
        """
    examples = """Examples:
  run_me.ps1 --env dev "hello"
  run_me.ps1 --env dev --offline "hello"
  run_me.ps1 --env dev recommend
  run_me.ps1 --env dev validate-workflows
  run_me.ps1 --env dev resume <run_id>

Commands:
  serve                         Start the OpenAI-compatible HTTP server.
  recommend                     Return follow-up question recommendations.
  validate-workflows            Validate configured workflow YAML files.
  resume <run_id>               Approve and continue an interrupted run.
  refuse <run_id>               Refuse an interrupted run.
"""
    parser = argparse.ArgumentParser(
        description="ZGraph agent runtime",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=examples,
    )
    parser.add_argument("text", nargs="*", help="Prompt text or a command such as serve, recommend, or validate-workflows.")
    parser.add_argument("--prompt", "-p", help="Prompt text.")
    parser.add_argument("--serve", action="store_true", help="Start OpenAI-compatible HTTP server.")
    parser.add_argument("--offline", action="store_true", help="Do not call the model provider.")
    parser.add_argument("--auto-approve", action="store_true", help="Auto-approve high-risk interrupts after guardian review.")
    parser.add_argument("--json", action="store_true", help="Print full runtime JSON.")
    return parser


def _approval_decision(text: str) -> bool | None:
    """内部方法：审批decision。
        参数:
            text: 文本（str）
        返回:
            返回类型为 bool | None 的结果
        """
    normalized = text.strip().lower()
    if normalized in {"yes", "y", "approve", "approved", "continue", "ok", "同意", "批准", "继续"}:
        return True
    if normalized in {"no", "n", "refuse", "reject", "deny", "cancel", "不同意", "拒绝", "取消"}:
        return False
    return None


def _print_cli_result(result: Any, as_json: bool, *, data_only: bool = False) -> None:
    """内部方法：打印cli结果。
        参数:
            result: 结果（Any）
            as_json: asJSON（bool）
        """
    if as_json:
        print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
        return
    if data_only and result.status == "completed":
        print(json.dumps(result.data or {"data": []}, ensure_ascii=False))
        return

    data = result.data if isinstance(result.data, dict) else {}
    if _is_workflow_validation_payload(data):
        print(_format_workflow_validation(data))
        return
    if _is_workflow_execution_payload(data):
        print(_format_workflow_execution(result))
        return

    content = str(getattr(result, "content", "") or "").strip()
    if content:
        print(content)
    if getattr(result, "status", "") not in {"completed", "interrupted", "refused"}:
        _print_result_error(result)


def _print_command_usage(command: str, description: str) -> None:
    """打印命令用量"""
    print(f"{description}\n")
    print(f"Usage: {command} <run_id>")
    print("\nExamples:")
    print(f"  .\\run_me.ps1 --env dev {command} <run_id>")
    print(f"  ./run_me.sh --env dev {command} <run_id>")


def _print_resume_hint(run_id: str) -> None:
    """打印继续提示"""
    print("Pending approval saved.")
    print("To continue after review:")
    print(f"  .\\run_me.ps1 resume {run_id}")
    print(f"  ./run_me.sh resume {run_id}")
    print("To refuse:")
    print(f"  .\\run_me.ps1 refuse {run_id}")
    print(f"  ./run_me.sh refuse {run_id}")


def _print_result_error(result: Any) -> None:
    """打印错误信息"""
    error = str(getattr(result, "error", "") or "").strip()
    if error:
        print(f"\nError: {error}")
    print("Run again with --json for the full RuntimeResult.")


def _is_workflow_validation_payload(data: dict[str, Any]) -> bool:
    """判断工作流校验载荷是否有效"""
    return isinstance(data.get("workflows"), list) and "valid" in data and "count" in data


def _format_workflow_validation(data: dict[str, Any]) -> str:
    """格式化工作流校验"""
    valid = bool(data.get("valid", False))
    count = int(data.get("count") or 0)
    workflows = data.get("workflows") if isinstance(data.get("workflows"), list) else []

    lines: list[str] = []
    if count == 0:
        lines.append("No configured workflows were found.")
        lines.append("Strict workflows are discovered from SKILL.md front matter or app-local workflow.yaml files.")
        lines.append("Add workflow metadata to a skill, then run validate-workflows again.")
        return "\n".join(lines)

    lines.append("Workflow validation passed." if valid else "Workflow validation failed.")
    lines.append(f"Checked {count} workflow(s).")
    for item in workflows:
        if not isinstance(item, dict):
            continue
        item_valid = bool(item.get("valid", False))
        label = "OK" if item_valid else "FAIL"
        workflow = str(item.get("workflow") or item.get("skill") or "<unknown>")
        skill = str(item.get("skill") or "<unknown>")
        lines.append("")
        lines.append(f"[{label}] {workflow} (skill: {skill})")
        source = str(item.get("source") or "").strip()
        if source:
            lines.append(f"  Source: {source}")
        required_inputs = item.get("required_inputs")
        if isinstance(required_inputs, list) and required_inputs:
            lines.append(f"  Required inputs: {', '.join(str(name) for name in required_inputs)}")
        errors = item.get("errors") if isinstance(item.get("errors"), list) else []
        if errors:
            lines.append("  Errors:")
            for error in errors:
                message = _first_error_line(str(error))
                lines.append(f"    - {message}")
                hint = _workflow_validation_hint(message)
                if hint:
                    lines.append(f"      Hint: {hint}")
    lines.append("")
    lines.append("Use --json for machine-readable details.")
    return "\n".join(lines)


def _first_error_line(error: str) -> str:
    """第一个错误line"""
    return next((line.strip() for line in error.splitlines() if line.strip()), error.strip())


def _workflow_validation_hint(error: str) -> str:
    """工作流校验提示"""
    lowered = error.lower()
    if "unknown tool" in lowered or "references unknown tool" in lowered:
        return "Change step.tool to a registered tool, or register the missing tool before validation."
    if "shell date command" in lowered:
        return "Replace shell date commands with the datetime tool so the workflow stays cross-platform."
    if "unsupported expression" in lowered:
        return "Use one of the supported output expressions: content, ok, data.*, json.*, or $.*."
    if ".needs references missing or later step" in lowered:
        return "Move the dependency step earlier, or fix the needs value to match an existing prior step id."
    if "args failed" in lowered and "schema validation" in lowered:
        return "Check that the step args match the tool schema. Use --json to see the full validation detail."
    if "must contain a yaml object" in lowered:
        return "Make the workflow file a YAML mapping with keys such as name, version, inputs, and steps."
    if "workflow.steps must contain at least one step" in lowered:
        return "Add at least one item under steps."
    return ""


def _is_workflow_execution_payload(data: dict[str, Any]) -> bool:
    """判断是否为工作流"""
    workflow = data.get("workflow")
    return isinstance(workflow, dict) and "status" in workflow


def _format_workflow_execution(result: Any) -> str:
    """格式化工作流"""
    data = result.data if isinstance(result.data, dict) else {}
    workflow = data.get("workflow") if isinstance(data.get("workflow"), dict) else {}
    plan = data.get("workflow_plan") if isinstance(data.get("workflow_plan"), dict) else {}
    status = str(workflow.get("status") or result.status or "unknown")
    name = str(workflow.get("name") or plan.get("workflow") or plan.get("skill") or "workflow")

    lines = [f"Workflow {name} {status}."]
    lines.append(f"Run ID: {result.run_id}")
    source = str(plan.get("source") or "").strip()
    if source:
        lines.append(f"Source: {source}")
    draft_path = str(plan.get("path") or "").strip()
    if draft_path:
        lines.append(f"Draft: {draft_path}")

    steps = workflow.get("steps") if isinstance(workflow.get("steps"), list) else []
    failed_step = _first_failed_step(steps)
    if failed_step:
        step_id = str(failed_step.get("id") or failed_step.get("name") or "<unknown>")
        tool = str(failed_step.get("tool") or "")
        attempts = failed_step.get("attempts")
        tool_part = f", tool: {tool}" if tool else ""
        attempts_part = f", attempts: {attempts}" if attempts else ""
        lines.append(f"Failed step: {step_id}{tool_part}{attempts_part}")
        error = str(failed_step.get("error") or failed_step.get("content") or "").strip()
        if error:
            lines.append(f"Step error: {_first_error_line(error)}")
            hint = _workflow_execution_hint(error)
            if hint:
                lines.append(f"Hint: {hint}")
    elif status == "completed":
        completed = sum(1 for step in steps if isinstance(step, dict) and step.get("status") == "completed")
        lines.append(f"Steps completed: {completed}/{len(steps)}")

    errors = workflow.get("errors") if isinstance(workflow.get("errors"), list) else []
    if errors:
        lines.append("Workflow errors:")
        for error in errors[:5]:
            lines.append(f"  - {_first_error_line(str(error))}")
        if len(errors) > 5:
            lines.append(f"  - ... {len(errors) - 5} more error(s)")

    log_path = _workflow_result_log_path(draft_path)
    if log_path:
        lines.append(f"Details: {log_path}")
    lines.append("Use --json for the full RuntimeResult.")
    return "\n".join(lines)


def _first_failed_step(steps: list[Any]) -> dict[str, Any] | None:
    """第一个failed步骤"""
    for step in steps:
        if isinstance(step, dict) and step.get("status") == "failed":
            return step
    return None


def _workflow_result_log_path(draft_path: str) -> str:
    """工作流结果记录路径"""
    if not draft_path:
        return ""
    try:
        path = Path(draft_path)
        if path.parent.name == "drafts":
            return str(path.parent.parent / "logs" / "workflow-result.json")
    except Exception:
        return ""
    return ""


def _workflow_execution_hint(error: str) -> str:
    """工作流执行提示"""
    lowered = error.lower()
    if "environment variable" in lowered and "not configured" in lowered:
        return "Check the selected env file and make sure run_me loaded the required variable."
    if "could not be extracted" in lowered:
        return "The adapter response did not match the configured output path; check adapters.yaml outputs against the API response."
    if "至少需要2个选项" in error:
        return "The question option list must contain at least two options before calling the backend."
    if "bash is disabled" in lowered:
        return "Run with --auto-approve or set ZGRAPH_ALLOW_BASH=true only when shell execution is expected."
    if "timed out" in lowered:
        return "Increase timeout_seconds for the step or check whether the backend endpoint is reachable."
    return ""


def main(argv: list[str] | None = None) -> int:
    """主入口"""
    _configure_stdio()
    parser = build_parser()
    args = parser.parse_args(argv)
    settings = Settings.from_env()
    if args.auto_approve:
        settings.auto_approve_interrupts = True
    command = args.text[0].lower() if args.text else ""
    if args.serve or command == "serve":
        if command == "serve":
            args.text = args.text[1:]
        return serve(settings)
    return run_cli(args, settings)


def _configure_stdio() -> None:
    """配置stdio"""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except Exception:
            pass


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
