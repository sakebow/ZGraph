from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


def _env(name: str, default: str = "") -> str:
    """读取字符串类型的环境变量"""
    return os.getenv(name, default).strip()


def _env_int(name: str, default: int) -> int:
    """读取整数类型的环境变量"""
    value = _env(name)
    if not value:
        return default
    try:
        return int(value)
    except ValueError:
        return default


def _env_float(name: str, default: Optional[float] = None) -> Optional[float]:
    """读取浮点数类型的环境变量"""
    value = _env(name)
    if not value:
        return default
    try:
        return float(value)
    except ValueError:
        return default


def _env_bool(name: str, default: bool = False) -> bool:
    """读取布尔类型的环境变量。
    参数:
        name: 环境变量名。
        default: 当环境变量不存在或为空时返回的默认值。
    返回:
        当环境变量值为 "1"/"true"/"yes"/"y"/"on" 时返回 True，否则返回 False。
    """
    value = _env(name)
    if not value:
        return default
    return value.lower() in {"1", "true", "yes", "y", "on"}


@dataclass(slots=True)
class Settings:
    """ZGraph 运行时配置集合。
    所有字段优先从环境变量读取，若未设置则使用默认值。
    通过 ``Settings.from_env()`` 从当前进程环境构建实例。
    """

    base_url: str
    """LLM 服务提供商基础 URL。对应环境变量 ``BASE_URL``。"""

    api_key: str
    """LLM 服务提供商 API key。对应环境变量 ``APIKEY`` 或 ``API_KEY``。"""

    model_name: str
    """LLM 模型名称。对应环境变量 ``LLM_MODEL_NAME`` 或 ``MODEL_NAME``。"""

    provider: str
    """LLM 提供商适配器名称。对应环境变量 ``LLM_PROVIDER``。"""

    timeout: int
    """LLM 请求超时时间（秒）。对应环境变量 ``LLM_TIMEOUT``。"""

    temperature: Optional[float]
    """LLM 采样温度。对应环境变量 ``LLM_TEMPERATURE``。"""

    top_p: Optional[float]
    """LLM nucleus sampling 参数。对应环境变量 ``LLM_TOP_P``。"""

    max_tokens: Optional[int]
    """LLM 最大生成 token 数。对应环境变量 ``LLM_MAX_TOKENS``。"""

    reasoning_effort: str
    """推理强度提示。对应环境变量 ``LLM_REASONING_EFFORT``。"""

    structured_output: bool
    """是否使用结构化输出。对应环境变量 ``STRUCTURED_OUTPUT``。"""

    max_rounds: int
    """Agent 最大执行轮数。对应环境变量 ``MAX_ROUNDS``。"""

    stream: bool
    """是否启用流式响应。对应环境变量 ``ZGRAPH_STREAM``。"""

    system_prompt: str
    """默认系统提示词。对应环境变量 ``SYSTEM_PROMPT``。"""

    host: str
    """HTTP 服务监听主机。对应环境变量 ``HOST``。"""

    port: int
    """HTTP 服务监听端口。对应环境变量 ``PORT``。"""

    zgraph_home: Path
    """ZGraph 根工作区目录。对应环境变量 ``ZGRAPH_HOME``。"""

    data_dir: Path
    """持久化数据目录。对应环境变量 ``ZGRAPH_DATA_DIR``。"""

    layer_config: Path
    """层配置文件路径。对应环境变量 ``ZGRAPH_LAYER_CONFIG``。"""

    skills_dir: Path
    """Skill 加载目录，默认位于 ``ZGRAPH_HOME/skills``。"""

    offline: bool
    """是否离线运行。对应环境变量 ``ZGRAPH_OFFLINE``。"""

    skill_search: bool
    """是否启用 skill 搜索。对应环境变量 ``SKILL_SEARCH``。"""

    skill_top_k: int
    """最多选择的 skill 数量。对应环境变量 ``SKILL_TOP_K``。"""

    skill_min_score: float
    """Skill 匹配最低分。对应环境变量 ``SKILL_MIN_SCORE``。"""

    skill_context_char_limit: int
    """注入系统提示词的 skill 文本最大字符数。对应环境变量 ``SKILL_CONTEXT_CHAR_LIMIT``。"""

    tokenizer_strategy: str
    """分词/检索策略，可选 ``rerank`` 或 ``word``。对应环境变量 ``ZGRAPH_TOKENIZER_STRATEGY``。"""

    rerank_model_name: str
    """Rerank 模型名称。对应环境变量 ``RERANK_MODEL_NAME``。"""

    rerank_base_url: str
    """Rerank 服务基础 URL。对应环境变量 ``RERANK_BASE_URL``。"""

    rerank_api_key: str
    """Rerank API key。对应环境变量 ``RERANK_API_KEY``。"""

    rerank_timeout: int
    """Rerank 请求超时（秒）。对应环境变量 ``RERANK_TIMEOUT``。"""

    rerank_document_char_limit: int
    """Rerank 单文档最大字符数。对应环境变量 ``RERANK_DOCUMENT_CHAR_LIMIT``。"""

    rerank_batch_size: int
    """Rerank 批大小。对应环境变量 ``RERANK_BATCH_SIZE``。"""

    tool_top_k: int
    """最多选择的工具数量。对应环境变量 ``TOOL_TOP_K``。"""

    tool_min_score: float
    """工具匹配最低分。对应环境变量 ``TOOL_MIN_SCORE``。"""

    memory_summary_max_tokens: int
    """记忆摘要最大 token 数。对应环境变量 ``MEMORY_SUMMARY_MAX_TOKENS``。"""

    memory_summary_temperature: Optional[float]
    """记忆摘要温度。对应环境变量 ``MEMORY_SUMMARY_TEMPERATURE``。"""

    log_level: str
    """日志级别。对应环境变量 ``ZGRAPH_LOG_LEVEL``。"""

    log_enabled: bool
    """是否启用日志。对应环境变量 ``ZGRAPH_LOG_ENABLED``。"""

    whitelist: set[str]
    """HTTP serve 模式下的 app_id/user 白名单集合。对应环境变量 ``WHITELIST``。"""

    run_ttl_seconds: int
    """运行工作区保留时间（秒），过期后会被清理。对应环境变量 ``ZGRAPH_RUN_TTL_SECONDS``。"""

    allow_bash: bool
    """是否允许 ``bash`` 工具执行 shell 命令。对应环境变量 ``ZGRAPH_ALLOW_BASH``。"""

    auto_approve_interrupts: bool
    """是否自动批准高风险中断。对应环境变量 ``ZGRAPH_AUTO_APPROVE_INTERRUPTS``。"""

    @classmethod
    def from_env(cls) -> "Settings":
        """从当前进程环境变量构建 ``Settings`` 实例"""
        cwd = Path.cwd()
        home = Path(_env("ZGRAPH_HOME", str(cwd / ".zgraph"))).expanduser()
        data_dir = Path(_env("ZGRAPH_DATA_DIR", str(home / "data"))).expanduser()
        layer_config = Path(
            _env("ZGRAPH_LAYER_CONFIG", str(cwd / "zgraph.config.default.yaml"))
        ).expanduser()
        skills_dir = home / "skills"

        whitelist_raw = _env("WHITELIST")
        whitelist = {item.strip() for item in whitelist_raw.split(",") if item.strip()}

        return cls(
            base_url=_env("BASE_URL"),
            api_key=_env("APIKEY") or _env("API_KEY"),
            model_name=_env("LLM_MODEL_NAME") or _env("MODEL_NAME", "gpt-4o-mini"),
            provider=_env("LLM_PROVIDER") or "openai",
            timeout=_env_int("LLM_TIMEOUT", 120),
            temperature=_env_float("LLM_TEMPERATURE", None),
            top_p=_env_float("LLM_TOP_P", None),
            max_tokens=_env_int("LLM_MAX_TOKENS", 0) or None,
            reasoning_effort=_env("LLM_REASONING_EFFORT"),
            structured_output=_env_bool("STRUCTURED_OUTPUT", False),
            max_rounds=_env_int("MAX_ROUNDS", 50),
            stream=_env_bool("ZGRAPH_STREAM", True),
            system_prompt=_env(
                "SYSTEM_PROMPT",
                "You are a helpful assistant. Use tools when needed and stop when the task is complete.",
            ),
            host=_env("HOST", "127.0.0.1"),
            port=_env_int("PORT", 8001),
            zgraph_home=home,
            data_dir=data_dir,
            layer_config=layer_config,
            skills_dir=skills_dir,
            offline=_env_bool("ZGRAPH_OFFLINE", False),
            skill_search=_env_bool("SKILL_SEARCH", True),
            skill_top_k=_env_int("SKILL_TOP_K", 4),
            skill_min_score=_env_float("SKILL_MIN_SCORE", 0.18) or 0.18,
            skill_context_char_limit=_env_int("SKILL_CONTEXT_CHAR_LIMIT", 1200),
            tokenizer_strategy=_env("ZGRAPH_TOKENIZER_STRATEGY", "word"),
            rerank_model_name=_env("RERANK_MODEL_NAME"),
            rerank_base_url=_env("RERANK_BASE_URL"),
            rerank_api_key=_env("RERANK_API_KEY"),
            rerank_timeout=_env_int("RERANK_TIMEOUT", 30),
            rerank_document_char_limit=_env_int("RERANK_DOCUMENT_CHAR_LIMIT", 1024),
            rerank_batch_size=_env_int("RERANK_BATCH_SIZE", 4),
            tool_top_k=_env_int("TOOL_TOP_K", 4),
            tool_min_score=_env_float("TOOL_MIN_SCORE", 0.18) or 0.18,
            memory_summary_max_tokens=_env_int("MEMORY_SUMMARY_MAX_TOKENS", 256),
            memory_summary_temperature=_env_float("MEMORY_SUMMARY_TEMPERATURE", 0.2),
            log_level=_env("ZGRAPH_LOG_LEVEL", "INFO"),
            log_enabled=_env_bool("ZGRAPH_LOG_ENABLED", True),
            whitelist=whitelist,
            run_ttl_seconds=_env_int("ZGRAPH_RUN_TTL_SECONDS", 24 * 60 * 60),
            allow_bash=_env_bool("ZGRAPH_ALLOW_BASH", False),
            auto_approve_interrupts=_env_bool("ZGRAPH_AUTO_APPROVE_INTERRUPTS", False),
        )
