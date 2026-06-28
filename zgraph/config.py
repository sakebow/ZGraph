from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


@dataclass(slots=True, frozen=True)
class ProviderConfig:
    """单个 LLM provider 的运行时配置。

    ``name`` 为 provider 的小写标识（如 ``deepseek``）；``base_url`` 与
    ``model`` 已根据 ``ZGRAPH_PROVIDERS`` 环境变量解析完毕，可直接传给
    OpenAI 兼容客户端。
    """

    name: str
    api_key: str
    base_url: str
    model: str

    @property
    def is_configured(self) -> bool:
        """provider 是否拥有可用的 API key。"""
        return bool(self.api_key)


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

    providers: dict[str, ProviderConfig]
    """已启用的 provider 配置字典，key 为 provider 小写名称。对应环境变量
    ``ZGRAPH_PROVIDERS``（逗号分隔）及每个 provider 各自的 ``<NAME>_API_KEY``、
    ``<NAME>_MODEL``、``<NAME>_BASE_URL``。若 ``ZGRAPH_PROVIDERS`` 未设置，则
    使用旧的 ``BASE_URL``/``APIKEY``/``LLM_MODEL_NAME`` 合成一个名为
    ``default`` 的 provider 以保持向后兼容。"""

    default_provider: str
    """当前默认 provider 名称。对应环境变量 ``ZGRAPH_DEFAULT_PROVIDER``；
    为空时取 ``providers`` 中第一个 key。"""

    legacy_provider: Optional[ProviderConfig]
    """从 ``BASE_URL``/``APIKEY``/``MODEL_NAME`` 解析得到的 legacy 单 provider。
    当 ``BASE_URL`` 与 ``APIKEY`` 都非空时填充，与 ``ZGRAPH_PROVIDERS`` 是否
    设置无关。该 provider 在 fallback 链中永远排在第一位（名为 ``__legacy__``），
    即使同时配置了 ``ZGRAPH_PROVIDERS``。"""

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

        legacy_base_url = _env("BASE_URL")
        legacy_api_key = _env("APIKEY") or _env("API_KEY")
        legacy_model = _env("LLM_MODEL_NAME") or _env("MODEL_NAME", "gpt-4o-mini")

        # legacy_provider 独立于 ZGRAPH_PROVIDERS：只要 BASE_URL+APIKEY 都非空就填充。
        # 在 fallback 链里它会永远排在第一位（名为 "__legacy__"）。
        legacy_provider: Optional[ProviderConfig] = None
        if legacy_base_url and legacy_api_key:
            legacy_provider = ProviderConfig(
                name="__legacy__",
                api_key=legacy_api_key,
                base_url=legacy_base_url,
                model=legacy_model,
            )

        providers = _parse_providers_from_env(
            legacy_base_url=legacy_base_url,
            legacy_api_key=legacy_api_key,
            legacy_model=legacy_model,
        )
        default_provider = _resolve_default_provider(providers)

        # 同步 default provider 的值到顶层 base_url/api_key/model_name，
        # 以保持 runtime.py / planner.py 等处对这些字段的旧引用仍然有效。
        default_cfg = providers.get(default_provider)
        if default_cfg is not None:
            base_url_out = default_cfg.base_url or legacy_base_url
            api_key_out = default_cfg.api_key or legacy_api_key
            model_name_out = default_cfg.model or legacy_model
        else:
            base_url_out = legacy_base_url
            api_key_out = legacy_api_key
            model_name_out = legacy_model

        return cls(
            base_url=base_url_out,
            api_key=api_key_out,
            model_name=model_name_out,
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
            providers=providers,
            default_provider=default_provider,
            legacy_provider=legacy_provider,
        )


# ---------------------------------------------------------------------
# Provider 配置解析
# ---------------------------------------------------------------------

# 默认 provider 预设。需要新增 provider 时在此处追加；调用方无需改代码。
# 统一使用小写 key，与 env 变量 ``ZGRAPH_PROVIDERS`` 解析逻辑保持一致。
_PROVIDER_DEFAULTS: dict[str, tuple[str, str]] = {
    # name -> (default_base_url, default_model)
    "deepseek": ("https://api.deepseek.com/v1", "deepseek-chat"),
    "minimax": ("https://api.minimax.chat/v1", "MiniMax-Text-01"),
    "kimi": ("https://api.moonshot.cn/v1", "moonshot-v1-8k"),
}


def _parse_providers_from_env(
    *,
    legacy_base_url: str,
    legacy_api_key: str,
    legacy_model: str,
) -> dict[str, ProviderConfig]:
    """根据环境变量解析 provider 配置字典。

    解析顺序：
    1. 读取 ``ZGRAPH_PROVIDERS``（逗号分隔的小写 provider 列表）。
    2. 若未设置，则回退到旧的 ``BASE_URL``/``APIKEY``/``LLM_MODEL_NAME``，
       合成名为 ``default`` 的单个 provider（向后兼容）。
    3. 对每个 provider 读取 ``<NAME>_API_KEY``、``<NAME>_MODEL``、``<NAME>_BASE_URL``，
       其中 ``base_url`` 和 ``model`` 缺省时使用 ``_PROVIDER_DEFAULTS`` 中的预设。
    """
    providers_raw = _env("ZGRAPH_PROVIDERS")
    if providers_raw:
        names = [n.strip().lower() for n in providers_raw.split(",") if n.strip()]
        configs: dict[str, ProviderConfig] = {}
        for name in names:
            default_base, default_model = _PROVIDER_DEFAULTS.get(
                name, ("", "")
            )
            api_key = _env(f"{name.upper()}_API_KEY")
            model = _env(f"{name.upper()}_MODEL") or default_model
            base_url = _env(f"{name.upper()}_BASE_URL") or default_base
            configs[name] = ProviderConfig(
                name=name,
                api_key=api_key,
                base_url=base_url,
                model=model,
            )
        return configs

    # 向后兼容：未设置 ZGRAPH_PROVIDERS 时，用旧字段合成一个 provider。
    # 这里保留旧的 base_url / model，不强行套用任何预设，避免破坏现有部署。
    return {
        "default": ProviderConfig(
            name="default",
            api_key=legacy_api_key,
            base_url=legacy_base_url,
            model=legacy_model,
        )
    }


def _resolve_default_provider(providers: dict[str, ProviderConfig]) -> str:
    """解析当前默认 provider 名称。

    优先使用 ``ZGRAPH_DEFAULT_PROVIDER``；若未设置或不在 providers 中，
    则返回字典中第一个 key（按 ``sorted`` 顺序以保证确定性）。
    """
    explicit = _env("ZGRAPH_DEFAULT_PROVIDER").strip().lower()
    if explicit and explicit in providers:
        return explicit
    if not providers:
        return ""
    return next(iter(sorted(providers.keys())))
