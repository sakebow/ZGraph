from __future__ import annotations

import fnmatch
import json
import locale
import mimetypes
import os
import re
import shutil
import subprocess
import urllib.error
import urllib.request
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field

from zgraph.core.tool.base import RuntimeTool, ToolResult


class PathArgs(BaseModel):
    """路径参数。继承自 BaseModel。"""
    path: str = Field(description="Path relative to the current run workspace subdirectory.")
    base: str = Field(default="tmp", description="Workspace subdirectory to resolve from.")


class ReadArgs(PathArgs):
    """读取参数。继承自 PathArgs。"""
    max_chars: int = Field(default=20000, ge=1, le=200000)


class WriteArgs(PathArgs):
    """写入参数。继承自 PathArgs。"""
    content: str
    overwrite: bool = False
    base: str = "outputs"


class DeleteArgs(PathArgs):
    """删除参数。继承自 PathArgs。"""
    recursive: bool = False


class UpdateArgs(PathArgs):
    """更新参数。继承自 PathArgs。"""
    old_text: str
    new_text: str
    count: int = Field(default=1, ge=1)
    base: str = "outputs"


class GlobArgs(BaseModel):
    """glob参数。继承自 BaseModel。"""
    pattern: str = Field(default="**/*")
    base: str = "tmp"


class BashArgs(BaseModel):
    """bash参数。继承自 BaseModel。"""
    command: str
    timeout_seconds: int = Field(default=30, ge=1, le=300)


class HttpArgs(BaseModel):
    """HTTP参数。继承自 BaseModel。"""
    model_config = ConfigDict(populate_by_name=True)

    url: str
    method: str = "GET"
    headers: dict[str, str] = Field(default_factory=dict)
    json_body: Any = Field(default=None, alias="json")
    body: str = ""
    timeout_seconds: int = Field(default=30, ge=1, le=300)


class DateTimeArgs(BaseModel):
    """日期时间参数。继承自 BaseModel。"""
    format: str = Field(
        default="YYYY-MM-DD",
        description=(
            "Output date/time format. Supports strftime directives like %Y-%m-%d, "
            "token formats like YYYY-MM-DD HH:mm:ss, and aliases: iso, date, time, "
            "datetime, timestamp, rfc3339."
        ),
    )
    timezone: str = Field(
        default="",
        description="Optional IANA timezone such as Asia/Shanghai. Empty means system local timezone.",
    )


class TodoArgs(BaseModel):
    """todo参数。继承自 BaseModel。"""
    items: list[str]


class MediaInputArgs(BaseModel):
    """媒体输入参数。继承自 BaseModel。"""
    path: str = Field(
        description=(
            "Absolute path, or path relative to zgraph_home (e.g. "
            "'storage/examples/foo.png'). Use this tool whenever the user references "
            "a media file on disk that the runtime should attach to the current run."
        ),
    )


class InterruptArgs(BaseModel):
    """中断参数。继承自 BaseModel。"""
    interrupt_id: str
    reason: str = ""


class SpawnArgs(BaseModel):
    """spawn参数。继承自 BaseModel。"""
    prompt: str
    role: str = "worker"


class ReadTool(RuntimeTool):
    """读取工具。继承自 RuntimeTool。"""
    name = "read"
    description = "Read a text file inside the current run workspace."
    risk_level = "low"
    tags = ("file", "read")
    args_schema = ReadArgs

    def run(self, path: str, base: str = "tmp", max_chars: int = 20000) -> ToolResult:
        """执行核心逻辑并返回结果"""
        target = self.context.workspace.resolve_user_path(path, base=base)
        if not target.exists() or not target.is_file():
            return ToolResult(False, f"File not found: {path}")
        text = target.read_text(encoding="utf-8", errors="replace")
        clipped = text[:max_chars]
        data = {"path": str(target), "truncated": len(text) > len(clipped)}
        return ToolResult(True, clipped, data)


class WriteTool(RuntimeTool):
    """写入工具。继承自 RuntimeTool。"""
    name = "write"
    description = "Write a text file inside the current run workspace."
    risk_level = "medium"
    tags = ("file", "write")
    args_schema = WriteArgs

    def run(
        self,
        path: str,
        content: str,
        overwrite: bool = False,
        base: str = "outputs",
    ) -> ToolResult:
        """执行核心逻辑并返回结果"""

        target = self.context.workspace.resolve_user_path(path, base=base)
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists() and not overwrite:
            return ToolResult(False, f"Refusing to overwrite existing file: {path}")
        target.write_text(content, encoding="utf-8")
        return ToolResult(True, f"Wrote {target}", {"path": str(target)})


class UpdateTool(RuntimeTool):
    """更新工具。继承自 RuntimeTool。"""
    name = "update"
    description = "Replace text in an existing workspace file."
    risk_level = "medium"
    tags = ("file", "write", "update")
    args_schema = UpdateArgs

    def run(
        self,
        path: str,
        old_text: str,
        new_text: str,
        count: int = 1,
        base: str = "outputs",
    ) -> ToolResult:
        """执行核心逻辑并返回结果"""

        target = self.context.workspace.resolve_user_path(path, base=base)
        if not target.exists() or not target.is_file():
            return ToolResult(False, f"File not found: {path}")
        text = target.read_text(encoding="utf-8", errors="replace")
        if old_text not in text:
            return ToolResult(False, "old_text was not found")
        updated = text.replace(old_text, new_text, count)
        target.write_text(updated, encoding="utf-8")
        return ToolResult(True, f"Updated {target}", {"path": str(target)})


class DeleteTool(RuntimeTool):
    """删除工具。继承自 RuntimeTool。"""
    name = "delete"
    description = "Delete a file or directory inside the current run workspace."
    risk_level = "high"
    tags = ("file", "delete")
    args_schema = DeleteArgs

    def run(self, path: str, base: str = "tmp", recursive: bool = False) -> ToolResult:
        """执行核心逻辑并返回结果"""
        target = self.context.workspace.resolve_user_path(path, base=base)
        if not target.exists():
            return ToolResult(False, f"Path not found: {path}")
        if target.is_dir():
            if not recursive:
                return ToolResult(False, "Directory delete requires recursive=true")
            shutil.rmtree(target)
        else:
            target.unlink()
        return ToolResult(True, f"Deleted {target}", {"path": str(target)})


class GlobTool(RuntimeTool):
    """glob工具。继承自 RuntimeTool。"""
    name = "glob"
    description = "List files inside a workspace subdirectory using a glob pattern."
    risk_level = "low"
    tags = ("file", "read", "search")
    args_schema = GlobArgs

    def run(self, pattern: str = "**/*", base: str = "tmp") -> ToolResult:
        """执行核心逻辑并返回结果"""
        root = self.context.workspace.resolve_user_path(".", base=base)
        matches: list[str] = []
        for path in root.rglob("*"):
            if path.is_file():
                rel = path.relative_to(root).as_posix()
                if fnmatch.fnmatch(rel, pattern) or path.match(pattern):
                    matches.append(rel)
        return ToolResult(True, "\n".join(matches), {"matches": matches})


class BashTool(RuntimeTool):
    """bash工具。继承自 RuntimeTool。"""
    name = "bash"
    description = "Run a shell command in the current run workspace when bash is enabled."
    risk_level = "high"
    tags = ("shell", "execute")
    args_schema = BashArgs

    def run(self, command: str, timeout_seconds: int = 30) -> ToolResult:
        """执行核心逻辑并返回结果"""
        if not self.context.allow_bash:
            return ToolResult(False, "bash is disabled for this runtime")
        env = _subprocess_env()
        expanded_command = _expand_env_references(command, env)
        shell_command = _shell_command(expanded_command, timeout_seconds=timeout_seconds)
        try:
            completed = subprocess.run(
                shell_command.command,
                shell=shell_command.use_shell,
                cwd=self.context.workspace.run_dir,
                capture_output=True,
                timeout=timeout_seconds,
                env=env,
            )
        except subprocess.TimeoutExpired:
            return ToolResult(
                False,
                f"command timed out after {timeout_seconds} seconds",
                {"returncode": None, "shell": shell_command.name, "timeout": True},
            )
        except Exception as exc:
            return ToolResult(
                False,
                _redact_sensitive_env_values(str(exc), env),
                {"returncode": None, "shell": shell_command.name},
            )
        output = _decode_process_output(completed.stdout) + _decode_process_output(completed.stderr)
        output = _redact_sensitive_env_values(output, env)
        return ToolResult(
            completed.returncode == 0,
            output.strip(),
            {"returncode": completed.returncode, "shell": shell_command.name},
        )


class HttpTool(RuntimeTool):
    """HTTP工具。继承自 RuntimeTool。"""
    name = "http"
    description = "Send an HTTP request with optional JSON body."
    risk_level = "high"
    tags = ("http", "api", "network", "external")
    args_schema = HttpArgs

    def run(
        self,
        url: str,
        method: str = "GET",
        headers: dict[str, str] | None = None,
        json_body: Any = None,
        body: str = "",
        timeout_seconds: int = 30,
    ) -> ToolResult:
        """执行核心逻辑并返回结果"""

        env = _subprocess_env()
        expanded_url = _expand_env_references(url, env)
        expanded_headers = {
            str(key): _expand_env_references(str(value), env)
            for key, value in (headers or {}).items()
        }
        payload: bytes | None = None
        if json_body is not None:
            payload = json.dumps(json_body, ensure_ascii=False).encode("utf-8")
            expanded_headers.setdefault("Content-Type", "application/json")
        elif body:
            payload = _expand_env_references(body, env).encode("utf-8")

        request = urllib.request.Request(
            expanded_url,
            data=payload,
            headers=expanded_headers,
            method=method.upper(),
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
                raw = response.read()
                text = _decode_process_output(raw)
                data = {
                    "status": response.status,
                    "reason": response.reason,
                    "headers": dict(response.headers.items()),
                }
                return ToolResult(200 <= response.status < 300, _redact_sensitive_env_values(text, env), data)
        except urllib.error.HTTPError as exc:
            text = _decode_process_output(exc.read())
            data = {
                "status": exc.code,
                "reason": exc.reason,
                "headers": dict(exc.headers.items()) if exc.headers else {},
            }
            return ToolResult(False, _redact_sensitive_env_values(text or str(exc), env), data)
        except urllib.error.URLError as exc:
            return ToolResult(False, _redact_sensitive_env_values(str(exc.reason), env), {"status": None})


class DateTimeTool(RuntimeTool):
    """日期时间工具。继承自 RuntimeTool。"""
    name = "datetime"
    description = (
        "Get the current date/time from the runtime clock. Use this tool for all relative dates "
        "such as today, tomorrow, yesterday, or current time; do not guess dates from prompt context."
    )
    risk_level = "low"
    tags = ("date", "time", "datetime", "today", "calendar")
    args_schema = DateTimeArgs

    def run(self, format: str = "YYYY-MM-DD", timezone: str = "") -> ToolResult:
        """执行核心逻辑并返回结果"""
        try:
            now = _current_datetime(timezone)
        except ValueError as exc:
            return ToolResult(False, str(exc), {"timezone": timezone})

        try:
            rendered = _format_datetime(now, format)
        except ValueError as exc:
            return ToolResult(False, str(exc), {"format": format})

        return ToolResult(
            True,
            rendered,
            {
                "format": format,
                "timezone": str(now.tzinfo or ""),
                "iso": now.isoformat(timespec="seconds"),
                "timestamp": int(now.timestamp()),
            },
        )


class SetTodoListTool(RuntimeTool):
    """todo列表工具。继承自 RuntimeTool。"""
    name = "settodolist"
    description = "Store a todo list for the current run."
    risk_level = "medium"
    tags = ("state", "todo")
    args_schema = TodoArgs

    def run(self, items: list[str]) -> ToolResult:
        """执行核心逻辑并返回结果"""
        self.context.todo_list = [{"id": index + 1, "item": item} for index, item in enumerate(items)]
        target = self.context.workspace.tmp_dir / "todo.json"
        target.write_text(json.dumps(self.context.todo_list, ensure_ascii=False, indent=2), encoding="utf-8")
        return ToolResult(True, "Todo list saved", {"todo": self.context.todo_list})


class ApproveInterruptTool(RuntimeTool):
    """批准中断工具。继承自 RuntimeTool。"""
    name = "approve-interrupt"
    description = "Approve a pending interrupt in this run."
    risk_level = "medium"
    tags = ("interrupt", "approval")
    args_schema = InterruptArgs

    def run(self, interrupt_id: str, reason: str = "") -> ToolResult:
        """执行核心逻辑并返回结果"""
        item = self.context.interrupts.get(interrupt_id)
        if item is None:
            return ToolResult(False, f"Unknown interrupt: {interrupt_id}")
        item["status"] = "approved"
        item["decision_reason"] = reason
        return ToolResult(True, f"Approved interrupt {interrupt_id}", item)


class RefuseInterruptTool(RuntimeTool):
    """拒绝中断工具。继承自 RuntimeTool。"""
    name = "refuse-interrupt"
    description = "Refuse a pending interrupt in this run."
    risk_level = "medium"
    tags = ("interrupt", "approval")
    args_schema = InterruptArgs

    def run(self, interrupt_id: str, reason: str = "") -> ToolResult:
        """执行核心逻辑并返回结果"""
        item = self.context.interrupts.get(interrupt_id)
        if item is None:
            return ToolResult(False, f"Unknown interrupt: {interrupt_id}")
        item["status"] = "refused"
        item["decision_reason"] = reason
        return ToolResult(True, f"Refused interrupt {interrupt_id}", item)


class SpawnTool(RuntimeTool):
    """spawn工具。继承自 RuntimeTool。"""
    name = "spawn"
    description = "Create a child-agent draft task artifact inside the run workspace."
    risk_level = "medium"
    tags = ("agent", "spawn", "draft")
    args_schema = SpawnArgs

    def run(self, prompt: str, role: str = "worker") -> ToolResult:
        """执行核心逻辑并返回结果"""
        child_id = uuid.uuid4().hex
        draft = {
            "child_id": child_id,
            "role": role,
            "prompt": prompt,
            "status": "drafted",
        }
        target = self.context.workspace.drafts_dir / f"spawn-{child_id}.json"
        target.write_text(json.dumps(draft, ensure_ascii=False, indent=2), encoding="utf-8")
        return ToolResult(True, f"Spawn draft created: {child_id}", draft)


class MediaInputTool(RuntimeTool):
    """媒体输入工具。继承自 RuntimeTool。

    桥接磁盘文件 → media_store：LLM 看到用户提到本地媒体时调它，
    bytes 走 ``context.emit_media`` 落到 ``runs/{run_id}/``，URL 经 SSE
    流回客户端。
    """

    name = "media_input"
    description = (
        "Load a media file (image / audio / video / file) from disk and attach it "
        "to the current run. Returns the URL where the uploaded file can be retrieved. "
        "Use this whenever the user references a media file on disk."
    )
    risk_level = "low"
    tags = ("media", "image", "audio", "video", "input", "attach")
    args_schema = MediaInputArgs

    def run(self, path: str) -> ToolResult:
        """执行核心逻辑并返回结果"""
        src = Path(path)
        if not src.is_absolute():
            zgraph_home = self.context.metadata.get("zgraph_home")
            if not zgraph_home:
                return ToolResult(
                    False,
                    "Relative path requires zgraph_home in ToolContext.metadata; "
                    "please pass an absolute path instead.",
                )
            src = Path(str(zgraph_home)) / path
        try:
            resolved = src.resolve()
        except OSError as exc:
            return ToolResult(False, f"Cannot resolve path: {exc}")
        if not resolved.is_file():
            return ToolResult(False, f"File not found: {resolved}")

        mime, _ = mimetypes.guess_type(str(resolved))
        mime = mime or "application/octet-stream"
        if mime.startswith("image/"):
            modality = "image"
        elif mime.startswith("audio/"):
            modality = "audio"
        elif mime.startswith("video/"):
            modality = "video"
        else:
            modality = "file"

        try:
            data = resolved.read_bytes()
        except OSError as exc:
            return ToolResult(False, f"Failed to read {resolved}: {exc}")

        event = self.context.emit_media(
            modality=modality,
            mime=mime,
            data=data,
            name=resolved.name,
        )
        return ToolResult(
            True,
            f"Attached {resolved.name} ({len(data)} bytes, {mime})",
            {
                "url": event.url,
                "block_id": event.block_id,
                "modality": modality,
                "mime": mime,
                "size_bytes": event.size_bytes,
                "expires_at": event.expires_at,
                "source_path": str(resolved),
            },
        )


DEFAULT_TOOL_TYPES: tuple[type[RuntimeTool], ...] = (
    DateTimeTool,
    BashTool,
    HttpTool,
    ReadTool,
    WriteTool,
    DeleteTool,
    UpdateTool,
    GlobTool,
    SetTodoListTool,
    ApproveInterruptTool,
    RefuseInterruptTool,
    SpawnTool,
    MediaInputTool,
)


_FORMAT_ALIASES = {
    "iso": "iso",
    "iso8601": "iso",
    "rfc3339": "iso",
    "date": "%Y-%m-%d",
    "time": "%H:%M:%S",
    "datetime": "%Y-%m-%d %H:%M:%S",
    "timestamp": "timestamp",
    "unix": "timestamp",
}


def _current_datetime(timezone: str) -> datetime:
    """内部方法：当前日期时间。
    
        参数:
            timezone: 时区（str）
    
        返回:
            返回类型为 datetime 的结果
        """
    now = datetime.now().astimezone()
    if not timezone:
        return now
    try:
        return now.astimezone(ZoneInfo(timezone))
    except ZoneInfoNotFoundError as exc:
        raise ValueError(f"Unknown timezone: {timezone}") from exc


def _format_datetime(value: datetime, format: str) -> str:
    """内部方法：格式化日期时间。
    
        参数:
            value: 值（datetime）
            format: 格式化（str）
    
        返回:
            返回类型为 str 的结果
        """
    requested = (format or "YYYY-MM-DD").strip()
    alias = _FORMAT_ALIASES.get(requested.lower())
    if alias == "iso":
        return value.isoformat(timespec="seconds")
    if alias == "timestamp":
        return str(int(value.timestamp()))
    if alias:
        return value.strftime(alias)
    if "%" in requested:
        return value.strftime(requested)

    converted = _datetime_token_format(requested)
    try:
        rendered = value.strftime(converted)
    except ValueError as exc:
        raise ValueError(f"Invalid datetime format: {format}") from exc
    return rendered.replace("{MS}", f"{value.microsecond // 1000:03d}")


def _datetime_token_format(format: str) -> str:
    """内部方法：日期时间令牌格式化。
    
        参数:
            format: 格式化（str）
    
        返回:
            返回类型为 str 的结果
        """
    converted = format.replace("SSS", "{MS}")
    replacements = (
        ("YYYY", "%Y"),
        ("YY", "%y"),
        ("MM", "%m"),
        ("DD", "%d"),
        ("HH", "%H"),
        ("hh", "%I"),
        ("mm", "%M"),
        ("ss", "%S"),
    )
    for token, directive in replacements:
        converted = converted.replace(token, directive)
    return converted


def _decode_process_output(value: bytes | str | None) -> str:
    """内部方法：解码处理输出。
    
        参数:
            value: 值（bytes | str | None）
    
        返回:
            返回类型为 str 的结果
        """
    if value is None:
        return ""
    if isinstance(value, str):
        return value

    encodings = ["utf-8", locale.getpreferredencoding(False), "gbk", "cp936"]
    seen: set[str] = set()
    for encoding in encodings:
        normalized = encoding.lower()
        if normalized in seen:
            continue
        seen.add(normalized)
        try:
            return value.decode(encoding)
        except UnicodeDecodeError:
            continue
        except LookupError:
            continue
    return value.decode("utf-8", errors="replace")


def _subprocess_env() -> dict[str, str]:
    """内部方法：subprocess环境变量。
    
        返回:
            返回类型为 dict[str, str] 的结果
        """
    env = os.environ.copy()
    env.setdefault("PYTHONIOENCODING", "utf-8")
    env.setdefault("PYTHONUTF8", "1")
    return env


class _ShellCommand(BaseModel):
    """shell命令。继承自 BaseModel。"""
    command: str | list[str]
    use_shell: bool
    name: str


def _shell_command(command: str, *, timeout_seconds: int) -> _ShellCommand:
    """内部方法：shell命令。
    
        参数:
            command: 命令（str）
    
        返回:
            返回类型为 _ShellCommand 的结果
        """
    bash = _find_working_bash(timeout_seconds=min(timeout_seconds, 5))
    if bash:
        return _ShellCommand(command=[bash, "-lc", command], use_shell=False, name="bash")
    return _ShellCommand(command=_normalize_for_system_shell(command), use_shell=True, name="system")


def _find_working_bash(*, timeout_seconds: int) -> str:
    """内部方法：查找workingbash。
    
        返回:
            返回类型为 str 的结果
        """
    candidates = [shutil.which("bash")]
    if os.name == "nt":
        candidates.extend(
            [
                r"C:\Program Files\Git\bin\bash.exe",
                r"C:\Program Files\Git\usr\bin\bash.exe",
            ]
        )
    for candidate in candidates:
        if not candidate:
            continue
        normalized = str(candidate).lower()
        if os.name == "nt" and normalized.endswith(r"\windows\system32\bash.exe"):
            continue
        if _is_working_bash(str(candidate), timeout_seconds=timeout_seconds):
            return str(candidate)
    return ""


def _is_working_bash(bash: str, *, timeout_seconds: int) -> bool:
    """内部方法：判断是否为workingbash。
    
        参数:
            bash: bash（str）
    
        返回:
            返回类型为 bool 的结果
        """
    try:
        completed = subprocess.run(
            [bash, "-lc", "printf zgraph-bash-ok"],
            capture_output=True,
            timeout=timeout_seconds,
            env=_subprocess_env(),
        )
    except Exception:
        return ""
    if completed.returncode != 0:
        return ""
    if _decode_process_output(completed.stdout).strip() != "zgraph-bash-ok":
        return False
    return True


def _normalize_for_system_shell(command: str) -> str:
    """内部方法：normalizefor系统shell。
    
        参数:
            command: 命令（str）
    
        返回:
            返回类型为 str 的结果
        """
    if os.name != "nt":
        return command
    normalized = re.sub(r"\\\s*\r?\n", " ", command)
    return " ".join(normalized.splitlines())


_ENV_REF_RE = re.compile(r"\$(?:\{(?P<braced>[A-Za-z_][A-Za-z0-9_]*)\}|(?P<plain>[A-Za-z_][A-Za-z0-9_]*))")


def _expand_env_references(command: str, env: dict[str, str]) -> str:
    """内部方法：展开环境变量references。
    
        参数:
            command: 命令（str）
            env: 环境变量（dict[str, str]）
    
        返回:
            返回类型为 str 的结果
        """
    def replace(match: re.Match[str]) -> str:
        """replace。
        
            参数:
                match: 匹配（re.Match[str]）
        
            返回:
                返回类型为 str 的结果
            """
        name = match.group("braced") or match.group("plain") or ""
        if name in env:
            return env[name]
        return match.group(0)

    return _ENV_REF_RE.sub(replace, command)


def _redact_sensitive_env_values(text: str, env: dict[str, str]) -> str:
    """内部方法：redactsensitive环境变量values。
    
        参数:
            text: 文本（str）
            env: 环境变量（dict[str, str]）
    
        返回:
            返回类型为 str 的结果
        """
    redacted = text
    sensitive_names = ("KEY", "TOKEN", "SECRET", "PASSWORD", "AUTH", "CREDENTIAL")
    values = [
        value
        for name, value in env.items()
        if value and len(value) >= 6 and any(marker in name.upper() for marker in sensitive_names)
    ]
    for value in sorted(set(values), key=len, reverse=True):
        redacted = redacted.replace(value, "******")
    return redacted
