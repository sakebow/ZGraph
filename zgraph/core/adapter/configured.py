from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from copy import deepcopy
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


_MISSING = object()
_TEMPLATE_RE = re.compile(
    r"\{\{\s*([A-Za-z_][A-Za-z0-9_.-]*(?:\s*\|\s*[A-Za-z_][A-Za-z0-9_]*)*)\s*\}\}"
)


@dataclass(slots=True)
class ConfiguredAdapterResult:
    """已配置适配器动作的执行结果。

        参数:
            ok: 是否执行成功。
            app: 所属应用名称。
            adapter: 适配器名称。
            action: 动作名称。
            data: 成功时返回的数据字典。
            status: HTTP 响应状态码。
            content: 原始响应内容文本。
            error: 失败时的错误信息。
            raw: 原始响应载荷对象。
    """

    ok: bool
    app: str
    adapter: str
    action: str
    data: dict[str, Any] = field(default_factory=dict)
    status: int | None = None
    content: str = ""
    error: str = ""
    raw: Any = None

    def to_payload(self) -> dict[str, Any]:
        """
        将执行结果转换为用于返回的载荷字典。
        成功时包含 data 字段，失败时包含 error 字段以及可选的 raw 字段。
        """
        payload: dict[str, Any] = {
            "app": self.app,
            "adapter": self.adapter,
            "action": self.action,
            "status": self.status,
        }
        if self.ok:
            payload["data"] = self.data
        else:
            payload["error"] = self.error
            if self.raw not in (None, ""):
                payload["raw"] = self.raw
        return payload


class ConfiguredAdapterEngine:
    """加载并执行 ZGRAPH_HOME/apps/*/adapters.yaml 中配置适配器动作的引擎。
    """

    def __init__(self, *, zgraph_home: Path) -> None:
        """初始化新的 ConfiguredAdapterEngine 实例。

            参数:
                zgraph_home: ZGraph 主目录路径，会展开用户目录符号。

        """
        self.zgraph_home = zgraph_home.expanduser()
        self.apps_dir = self.zgraph_home / "apps"

    def call(
        self,
        *,
        app: str,
        adapter: str,
        action: str,
        params: dict[str, Any] | None = None,
        timeout_seconds: int = 60,
    ) -> ConfiguredAdapterResult:
        """调用已配置适配器的指定动作。
            执行流程包括：查找适配器配置、参数归一化、参数校验、
            发送 HTTP 请求、业务成功判断、输出字段提取。
            参数:
                app: 应用标识符，用于定位 adapters.yaml 文件。
                adapter: 适配器标识符。
                action: 动作标识符。
                params: 动作输入参数字典。
                timeout_seconds: 请求超时时间，单位为秒。
            返回:
                执行结果对象。
        """

        params = dict(params or {})
        try:
            app_name, adapter_config = self._find_adapter(app=app, adapter=adapter)
            action_config = _action_config(adapter_config, action)
            params, normalization_errors = _normalize_params(params, action_config)
            if normalization_errors:
                return ConfiguredAdapterResult(
                    False,
                    app_name,
                    adapter,
                    action,
                    error="adapter input normalization failed: " + "; ".join(normalization_errors),
                )

            validation_errors = _validate_params(params, action_config)
            if validation_errors:
                return ConfiguredAdapterResult(
                    False,
                    app_name,
                    adapter,
                    action,
                    error="adapter input validation failed: " + "; ".join(validation_errors),
                )

            request_config = _request_config(adapter_config, action_config)
            request_result = _send_request(
                adapter_config=adapter_config,
                action_config=action_config,
                request_config=request_config,
                params=params,
                timeout_seconds=timeout_seconds,
            )
            if request_result.error:
                return ConfiguredAdapterResult(
                    False,
                    app_name,
                    adapter,
                    action,
                    status=request_result.status,
                    content=request_result.text,
                    error=request_result.error,
                    raw=request_result.payload,
                )

            if not _business_ok(
                request_result.payload,
                http_ok=200 <= int(request_result.status or 0) < 300,
                adapter_config=adapter_config,
                action_config=action_config,
            ):
                return ConfiguredAdapterResult(
                    False,
                    app_name,
                    adapter,
                    action,
                    status=request_result.status,
                    content=request_result.text,
                    error=_business_error(request_result.payload, adapter_config, action_config),
                    raw=request_result.payload,
                )

            output_data, output_error = _extract_outputs(request_result.payload, action_config, params=params)
            if output_error:
                return ConfiguredAdapterResult(
                    False,
                    app_name,
                    adapter,
                    action,
                    status=request_result.status,
                    content=request_result.text,
                    error=output_error,
                    raw=request_result.payload,
                )
            return ConfiguredAdapterResult(
                True,
                app_name,
                adapter,
                action,
                data=output_data,
                status=request_result.status,
                content=request_result.text,
                raw=request_result.payload,
            )
        except Exception as exc:
            return ConfiguredAdapterResult(False, app or "", adapter, action, error=str(exc))

    def _find_adapter(self, *, app: str, adapter: str) -> tuple[str, dict[str, Any]]:
        """根据应用和适配器名称查找适配器配置。
            支持在指定应用目录下查找，也支持在全部应用目录中搜索。
            适配器名称匹配时不区分大小写。
            参数:
                app: 应用标识符，为空时搜索所有应用。
                adapter: 适配器标识符。
            返回:
                找到的应用名称与适配器配置字典。
            异常:
                ValueError: 当未找到指定适配器时抛出。
        """
        for app_name, path in self._candidate_adapter_files(app):
            payload = yaml.safe_load(path.read_text(encoding="utf-8", errors="replace")) or {}
            if not isinstance(payload, dict):
                continue
            adapters = payload.get("adapters") or {}
            if not isinstance(adapters, dict):
                continue
            if adapter in adapters and isinstance(adapters[adapter], dict):
                return app_name, adapters[adapter]
            lowered = {str(key).lower(): key for key in adapters}
            key = lowered.get(adapter.lower())
            if key is not None and isinstance(adapters[key], dict):
                return app_name, adapters[key]
        app_hint = app or "any app"
        raise ValueError(f"adapter {adapter!r} was not found in {app_hint}")

    def _candidate_adapter_files(self, app: str) -> list[tuple[str, Path]]:
        """获取候选适配器配置文件列表。
            参数:
                app: 应用标识符，为空时返回所有应用目录下的文件。
            返回:
                应用名称与适配器文件路径的元组列表。
        """
        candidates: list[tuple[str, Path]] = []
        if app:
            app_dir = self.apps_dir / app
            candidates.extend((app, app_dir / name) for name in ("adapters.yaml", "adapters.yml"))
            return [(name, path) for name, path in candidates if path.exists() and path.is_file()]

        if not self.apps_dir.exists():
            return []
        for app_dir in sorted(self.apps_dir.iterdir(), key=lambda item: str(item).lower()):
            if not app_dir.is_dir():
                continue
            for name in ("adapters.yaml", "adapters.yml"):
                path = app_dir / name
                if path.exists() and path.is_file():
                    candidates.append((app_dir.name, path))
        return candidates


@dataclass(slots=True)
class _HttpResult:
    """HTTP 请求结果内部对象。
        参数:
            status: HTTP 状态码，可能为 None。
            text: 响应体文本。
            payload: 解析后的响应载荷。
            error: 错误信息，为空表示请求成功。
    """

    status: int | None
    text: str
    payload: Any
    error: str = ""


def _action_config(adapter_config: dict[str, Any], action: str) -> dict[str, Any]:
    """从适配器配置中提取指定动作的配置。
        参数:
            adapter_config: 适配器配置字典。
            action: 动作标识符。
        返回:
            动作配置字典。
        异常:
            ValueError: 当动作配置不存在或格式不正确时抛出。
    """
    actions = adapter_config.get("actions") or {}
    if not isinstance(actions, dict):
        raise ValueError("adapter.actions must be an object")
    config = actions.get(action)
    if config is None:
        lowered = {str(key).lower(): key for key in actions}
        key = lowered.get(action.lower())
        config = actions.get(key) if key is not None else None
    if not isinstance(config, dict):
        raise ValueError(f"adapter action {action!r} was not found")
    return config


def _request_config(adapter_config: dict[str, Any], action_config: dict[str, Any]) -> dict[str, Any]:
    """获取动作的请求配置。
        优先使用 action_config 中定义的 request，否则返回空字典。
        参数:
            adapter_config: 适配器配置字典。
            action_config: 动作配置字典。
        返回:
            请求配置字典。
    """
    request = action_config.get("request")
    if isinstance(request, dict):
        return request
    return {}


def _send_request(
    *,
    adapter_config: dict[str, Any],
    action_config: dict[str, Any],
    request_config: dict[str, Any],
    params: dict[str, Any],
    timeout_seconds: int,
) -> _HttpResult:
    """根据配置发送 HTTP 请求并返回结果。
        参数:
            adapter_config: 适配器配置字典。
            action_config: 动作配置字典。
            request_config: 请求配置字典。
            params: 已渲染的请求参数字典。
            timeout_seconds: 请求超时时间，单位为秒。
        返回:
            HTTP 请求结果对象。
    """

    base_url = _configured_value(action_config, adapter_config, "base_url", "base_url_env").rstrip("/")
    if not base_url:
        return _HttpResult(None, "", None, "adapter base URL is not configured")

    path = _render_value(action_config.get("path", ""), params=params)
    if not path:
        return _HttpResult(None, "", None, "adapter action path is not configured")
    url = f"{base_url}{path if str(path).startswith('/') else '/' + str(path)}"

    method = str(action_config.get("method") or adapter_config.get("method") or "POST").upper()
    headers = _headers(adapter_config, action_config)
    body: bytes | None = None
    if "json" in request_config:
        rendered = _render_value(request_config.get("json"), params=params)
        body = json.dumps(rendered, ensure_ascii=False).encode("utf-8")
        headers.setdefault("Content-Type", "application/json")
    elif "body" in request_config:
        rendered_body = _render_value(request_config.get("body"), params=params)
        body = str(rendered_body).encode("utf-8")

    request = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            text = response.read().decode("utf-8", errors="replace")
            return _HttpResult(response.status, text, _parse_json(text))
    except urllib.error.HTTPError as exc:
        text = exc.read().decode("utf-8", errors="replace")
        return _HttpResult(exc.code, text, _parse_json(text), _business_error(_parse_json(text), adapter_config, action_config) or str(exc))
    except urllib.error.URLError as exc:
        return _HttpResult(None, "", None, str(exc.reason))


def _configured_value(primary: dict[str, Any], fallback: dict[str, Any], value_key: str, env_key: str) -> str:
    """从主配置或回退配置中读取配置值。
        优先顺序为：主配置 value_key、主配置 env_key 对应的环境变量、
        回退配置 value_key、回退配置 env_key 对应的环境变量。
        参数:
            primary: 主配置字典。
            fallback: 回退配置字典。
            value_key: 直接配置值的键名。
            env_key: 环境变量名称的键名。
        返回:
            配置值字符串。
    """
    if primary.get(value_key) not in (None, ""):
        return str(primary.get(value_key)).strip()
    if primary.get(env_key) not in (None, ""):
        return _env(str(primary.get(env_key)))
    if fallback.get(value_key) not in (None, ""):
        return str(fallback.get(value_key)).strip()
    if fallback.get(env_key) not in (None, ""):
        return _env(str(fallback.get(env_key)))
    return ""


def _env(name: str) -> str:
    """读取指定名称的环境变量值。"""
    value = os.getenv(name, "").strip()
    if not value:
        raise ValueError(f"environment variable {name} is not configured")
    return value


def _headers(adapter_config: dict[str, Any], action_config: dict[str, Any]) -> dict[str, str]:
    """构造 HTTP 请求头。
        合并适配器级与动作级 headers，并可选添加认证头。
        参数:
            adapter_config: 适配器配置字典。
            action_config: 动作配置字典。
        返回:
            请求头字典。
    """
    headers: dict[str, str] = {}
    for source in (adapter_config.get("headers"), action_config.get("headers")):
        if isinstance(source, dict):
            headers.update({str(key): str(value) for key, value in source.items()})

    auth = action_config.get("auth") if isinstance(action_config.get("auth"), dict) else adapter_config.get("auth")
    if isinstance(auth, dict):
        header = str(auth.get("header") or "").strip()
        value = ""
        if auth.get("value_env") not in (None, ""):
            value = _env(str(auth.get("value_env")))
        elif auth.get("value") not in (None, ""):
            value = str(auth.get("value")).strip()
        if header and value:
            headers[header] = value
    return headers


def _business_ok(
    payload: Any,
    *,
    http_ok: bool,
    adapter_config: dict[str, Any],
    action_config: dict[str, Any],
) -> bool:
    """判断业务层面是否成功。
        在 HTTP 状态码成功的基础上，根据配置中的 success 条件进一步判断。
        支持 any、all 以及单条件匹配。
        参数:
            payload: 响应载荷对象。
            http_ok: HTTP 状态码是否在成功范围。
            adapter_config: 适配器配置字典。
            action_config: 动作配置字典。
        返回:
            业务是否成功。
    """

    if not http_ok:
        return False
    success = action_config.get("success") or adapter_config.get("success")
    if not isinstance(success, dict):
        return http_ok
    if "any" in success and isinstance(success["any"], list):
        return any(_condition_matches(payload, condition) for condition in success["any"])
    if "all" in success and isinstance(success["all"], list):
        return all(_condition_matches(payload, condition) for condition in success["all"])
    return _condition_matches(payload, success)


def _condition_matches(payload: Any, condition: Any) -> bool:
    """判断响应载荷是否满足单个条件。
        支持 exists、equals、in 以及真值判断。
        参数:
            payload: 响应载荷对象。
            condition: 条件对象。
        返回:
            条件是否匹配。
    """
    if not isinstance(condition, dict):
        return bool(condition)
    value = payload if not condition.get("path") else _dig(payload, str(condition["path"]).split("."))
    if "exists" in condition:
        exists = value is not _MISSING and value not in (None, "", [], {})
        return exists is bool(condition["exists"])
    if value is _MISSING:
        return False
    if "equals" in condition:
        return _strict_equal(value, condition["equals"])
    if "in" in condition and isinstance(condition["in"], list):
        return any(_strict_equal(value, item) for item in condition["in"])
    return bool(value)


def _strict_equal(left: Any, right: Any) -> bool:
    """严格比较两个值是否相等。
        布尔值、数值和字符串等会区分类型，避免 Python 中 bool 与 int 的隐式相等。
        参数:
            left: 左侧值。
            right: 右侧值。
        返回:
            两值是否严格相等。
    """
    if isinstance(left, bool) or isinstance(right, bool):
        return type(left) is type(right) and left == right
    if isinstance(left, (int, float)) and isinstance(right, (int, float)):
        return left == right
    return type(left) is type(right) and left == right


def _business_error(payload: Any, adapter_config: dict[str, Any], action_config: dict[str, Any]) -> str:
    """从响应载荷中提取业务错误信息。
        优先根据配置中的 error.message 路径提取，否则尝试常见字段，
        最后返回截断后的 JSON 文本。
        参数:
            payload: 响应载荷对象。
            adapter_config: 适配器配置字典。
            action_config: 动作配置字典。
        返回:
            业务错误信息字符串。
    """
    message_config = _deep_get(action_config, ["error", "message"])
    if message_config is _MISSING:
        message_config = _deep_get(adapter_config, ["error", "message"])
    if message_config is not _MISSING:
        message = _extract_one(payload, message_config)
        if message not in (_MISSING, None, "", [], {}):
            return str(message)

    if isinstance(payload, dict):
        for key in ("message", "msg", "error", "errorMessage", "detail"):
            value = payload.get(key)
            if value not in (None, ""):
                return str(value)
        return json.dumps(payload, ensure_ascii=False)[:1000]
    return str(payload)[:1000]


def _extract_outputs(
    payload: Any,
    action_config: dict[str, Any],
    *,
    params: dict[str, Any],
) -> tuple[dict[str, Any], str]:
    """根据动作配置从响应载荷中提取输出字段。
        参数:
            payload: 响应载荷对象。
            action_config: 动作配置字典。
            params: 渲染输出时使用的参数字典。
        返回:
            提取到的数据字典与错误信息字符串；成功时错误信息为空。
    """

    outputs = action_config.get("outputs") or {}
    if not isinstance(outputs, dict):
        return {}, "adapter action outputs must be an object"

    data: dict[str, Any] = {}
    for name, spec in outputs.items():
        required = True
        if isinstance(spec, dict):
            required = bool(spec.get("required", True))
            if "value" in spec:
                value = _render_value(spec["value"], params=params)
            else:
                value = _extract_one(payload, spec)
        else:
            value = _dig(payload, str(spec).split("."))
        if value is _MISSING:
            if required:
                return {}, f"adapter output {name!r} could not be extracted"
            continue
        if isinstance(spec, dict) and spec.get("coerce"):
            value = _coerce_output(value, str(spec.get("coerce")))
        data[str(name)] = value
    return data, ""


def _extract_one(payload: Any, spec: Any) -> Any:
    """从响应载荷中按规范提取单个值。
        支持通过 path 或 first 列表提取，并可选仅返回标量值。
        参数:
            payload: 响应载荷对象。
            spec: 提取规范，可以是字符串路径或字典。
        返回:
            提取到的值，未找到时返回 _MISSING。
    """
    if isinstance(spec, dict):
        scalar_only = bool(spec.get("scalar", False))
        if "path" in spec:
            return _filter_scalar(_dig(payload, str(spec["path"]).split(".")), scalar_only=scalar_only)
        if "first" in spec and isinstance(spec["first"], list):
            for path in spec["first"]:
                value = _dig(payload, str(path).split("."))
                value = _filter_scalar(value, scalar_only=scalar_only)
                if value not in (_MISSING, None, "", [], {}):
                    return value
            return _MISSING
    if isinstance(spec, str):
        return _dig(payload, spec.split("."))
    return _MISSING


def _filter_scalar(value: Any, *, scalar_only: bool) -> Any:
    """过滤标量值。
        当 scalar_only 为 True 且值为 dict 或 list 时返回 _MISSING，否则原样返回。
        参数:
            value: 待过滤的值。
            scalar_only: 是否只保留标量值。
        返回:
            过滤后的值。
    """
    if not scalar_only:
        return value
    if isinstance(value, (dict, list)):
        return _MISSING
    return value


def _coerce_output(value: Any, target_type: str) -> Any:
    """将输出值强制转换为目标类型。
        支持 string、integer、number、boolean 四种类型。
        参数:
            value: 待转换的值。
            target_type: 目标类型名称。
        返回:
            转换后的值。
    """
    normalized = target_type.strip().lower()
    if normalized == "string":
        return str(value)
    if normalized == "integer":
        return int(value)
    if normalized == "number":
        return float(value)
    if normalized == "boolean":
        if isinstance(value, str):
            return value.strip().lower() in {"1", "true", "yes", "y", "on"}
        return bool(value)
    return value


def _validate_params(params: dict[str, Any], action_config: dict[str, Any]) -> list[str]:
    """根据动作配置校验输入参数。
        依次执行 JSON Schema 校验与自定义规则校验。
        参数:
            params: 输入参数字典。
            action_config: 动作配置字典。
        返回:
            校验错误信息列表，无错误时为空列表。
    """
    errors: list[str] = []
    schema = action_config.get("input_schema") or {}
    if isinstance(schema, dict) and schema:
        errors.extend(_validate_schema(params, schema, path="params"))
    for rule in action_config.get("rules") or []:
        if isinstance(rule, dict):
            error = _validate_rule(params, rule)
            if error:
                errors.append(error)
    return errors


def _normalize_params(params: dict[str, Any], action_config: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """对输入参数执行归一化处理。
        按照动作配置中的 normalizers 依次应用映射与类型强制转换。
        参数:
            params: 输入参数字典。
            action_config: 动作配置字典。
        返回:
            归一化后的参数字典与错误信息列表。
    """
    normalized = deepcopy(params)
    errors: list[str] = []
    normalizers = action_config.get("normalizers") or []
    if not isinstance(normalizers, list):
        return normalized, ["normalizers must be a list"]
    for index, normalizer in enumerate(normalizers):
        if not isinstance(normalizer, dict):
            errors.append(f"normalizers[{index}] must be an object")
            continue
        errors.extend(_apply_normalizer(normalized, normalizer, index=index))
    return normalized, errors


def _apply_normalizer(params: dict[str, Any], normalizer: dict[str, Any], *, index: int) -> list[str]:
    """对参数中的指定路径应用单个归一化器。
        参数:
            params: 待归一化的参数字典。
            normalizer: 归一化器配置字典。
            index: 归一化器在列表中的索引。
        返回:
            归一化过程中产生的错误信息列表。
    """
    path = str(normalizer.get("path") or "").strip()
    if not path:
        return [f"normalizers[{index}].path is required"]
    refs = _target_refs(params, path.split("."))
    if not refs:
        if normalizer.get("required", True):
            return [f"normalizers[{index}] did not match path {path!r}"]
        return []

    errors: list[str] = []
    for container, key, value in refs:
        changed, new_value, error = _normalized_value(value, normalizer)
        if error:
            errors.append(f"{path}: {error}")
            continue
        if changed:
            container[key] = new_value
    return errors


def _normalized_value(value: Any, normalizer: dict[str, Any]) -> tuple[bool, Any, str]:
    """对单个值执行归一化。
        先尝试按 mapping 进行映射，再按 coerce 进行类型强制转换。
        参数:
            value: 待归一化的值。
            normalizer: 归一化器配置字典。
        返回:
            是否发生变化、归一化后的值与错误信息字符串。
    """
    changed = False
    if isinstance(normalizer.get("map"), dict):
        mapped, new_value = _mapped_value(value, normalizer["map"])
        if mapped:
            changed = True
            value = new_value
        elif str(normalizer.get("on_unmatched") or "keep").lower() == "error":
            return False, value, f"unexpected value {value!r}"

    if normalizer.get("coerce"):
        try:
            coerced = _coerce_input(value, str(normalizer["coerce"]))
        except (TypeError, ValueError) as exc:
            return False, value, str(exc)
        return coerced != value or type(coerced) is not type(value), coerced, ""

    return changed, value, ""


def _mapped_value(value: Any, mapping: dict[Any, Any]) -> tuple[bool, Any]:
    """根据映射表将值映射为新的值。
        比较时进行严格相等比较；对于字符串值会忽略大小写和前后空白。
        参数:
            value: 待映射的值。
            mapping: 映射表字典。
        返回:
            是否发生映射以及映射后的值。
    """
    for raw_key, mapped in mapping.items():
        if _strict_equal(value, raw_key):
            return True, mapped
        if isinstance(raw_key, str) and isinstance(value, str) and value.strip().lower() == raw_key.strip().lower():
            return True, mapped
    return False, value


def _coerce_input(value: Any, target_type: str) -> Any:
    """将输入值强制转换为目标类型。
        支持 string、integer、number、boolean 四种类型，
        boolean 类型不会被强制转为 integer 或 number。
        参数:
            value: 待转换的值。
            target_type: 目标类型名称。
        返回:
            转换后的值。
        异常:
            ValueError: 当无法转换或目标类型不支持时抛出。
    """
    normalized = target_type.strip().lower()
    if normalized == "string":
        return str(value)
    if normalized == "integer":
        if isinstance(value, bool):
            raise ValueError("boolean cannot be coerced to integer without an explicit map")
        if isinstance(value, int):
            return value
        if isinstance(value, float) and value.is_integer():
            return int(value)
        if isinstance(value, str) and re.fullmatch(r"[-+]?\d+", value.strip()):
            return int(value.strip())
        raise ValueError(f"{value!r} cannot be coerced to integer")
    if normalized == "number":
        if isinstance(value, bool):
            raise ValueError("boolean cannot be coerced to number")
        if isinstance(value, (int, float)):
            return value
        if isinstance(value, str):
            stripped = value.strip()
            try:
                return int(stripped) if re.fullmatch(r"[-+]?\d+", stripped) else float(stripped)
            except ValueError as exc:
                raise ValueError(f"{value!r} cannot be coerced to number") from exc
        raise ValueError(f"{value!r} cannot be coerced to number")
    if normalized == "boolean":
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            lowered = value.strip().lower()
            if lowered in {"1", "true", "yes", "y", "on"}:
                return True
            if lowered in {"0", "false", "no", "n", "off"}:
                return False
        raise ValueError(f"{value!r} cannot be coerced to boolean")
    raise ValueError(f"unsupported coerce target {target_type!r}")


def _validate_schema(value: Any, schema: dict[str, Any], *, path: str) -> list[str]:
    """根据 JSON Schema 校验值。
        支持 object、array、string、integer、number、boolean 类型，
        以及 required、properties、enum、min_items、max_items、items 等约束。
        参数:
            value: 待校验的值。
            schema: JSON Schema 字典。
            path: 当前字段路径，用于生成错误信息。
        返回:
            校验错误信息列表。
    """
    errors: list[str] = []
    expected_type = str(schema.get("type") or ("object" if "properties" in schema or "required" in schema else "")).lower()
    if expected_type and not _type_matches(value, expected_type):
        return [str(schema.get("message") or f"{path} must be {expected_type}")]
    if isinstance(schema.get("enum"), list) and not any(_strict_equal(value, item) for item in schema["enum"]):
        return [str(schema.get("message") or f"{path} must be one of {schema['enum']!r}")]

    if expected_type == "object" and isinstance(value, dict):
        for name in schema.get("required") or []:
            item = value.get(name)
            if item in (None, "", [], {}):
                errors.append(str(schema.get("message") or f"{path}.{name} is required"))
        properties = schema.get("properties") or {}
        if isinstance(properties, dict):
            for name, child_schema in properties.items():
                if name in value and isinstance(child_schema, dict):
                    errors.extend(_validate_schema(value[name], child_schema, path=f"{path}.{name}"))

    if expected_type == "array" and isinstance(value, list):
        min_items = schema.get("min_items", schema.get("minItems"))
        if min_items is not None and len(value) < int(min_items):
            errors.append(str(schema.get("message") or f"{path} requires at least {min_items} items"))
        max_items = schema.get("max_items", schema.get("maxItems"))
        if max_items is not None and len(value) > int(max_items):
            errors.append(str(schema.get("message") or f"{path} allows at most {max_items} items"))
        item_schema = schema.get("item") or schema.get("items")
        if isinstance(item_schema, dict):
            for index, item in enumerate(value):
                errors.extend(_validate_schema(item, item_schema, path=f"{path}.{index}"))
    return errors


def _type_matches(value: Any, expected_type: str) -> bool:
    """判断值的类型是否与期望类型一致。
        参数:
            value: 待判断的值。
            expected_type: 期望类型名称。
        返回:
            类型是否匹配。
    """
    if expected_type == "object":
        return isinstance(value, dict)
    if expected_type == "array":
        return isinstance(value, list)
    if expected_type == "string":
        return isinstance(value, str)
    if expected_type == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected_type == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if expected_type == "boolean":
        return isinstance(value, bool)
    return True


def _validate_rule(params: dict[str, Any], rule: dict[str, Any]) -> str:
    """根据自定义规则校验参数。
        当前支持 exactly_one 规则，要求数组中恰好有一项满足 where 条件。
        参数:
            params: 输入参数字典。
            rule: 自定义规则字典。
        返回:
            校验错误信息字符串，通过时返回空字符串。
    """
    target = _dig(params, str(rule.get("path") or "").split(".")) if rule.get("path") else params
    if "exactly_one" in rule and isinstance(rule["exactly_one"], dict):
        if not isinstance(target, list):
            return str(rule.get("message") or f"{rule.get('path')} must be an array")
        where = rule["exactly_one"].get("where") or {}
        count = sum(1 for item in target if _condition_matches(item, where))
        if count != 1:
            return str(rule.get("message") or f"{rule.get('path')} must contain exactly one matching item")
    return ""


def _render_value(value: Any, *, params: dict[str, Any]) -> Any:
    """递归渲染值中的模板占位符。
        参数:
            value: 待渲染的值，可以是字符串、字典、列表或其他类型。
            params: 用于替换占位符的参数字典。
        返回:
            渲染后的值。
    """
    if isinstance(value, str):
        return _render_string(value, params=params)
    if isinstance(value, dict):
        return {str(key): _render_value(item, params=params) for key, item in value.items()}
    if isinstance(value, list):
        return [_render_value(item, params=params) for item in value]
    return value


def _render_string(value: str, *, params: dict[str, Any]) -> Any:
    """渲染字符串中的模板占位符。
        当整个字符串为单一占位符时返回原始值类型；
        否则将占位符替换为字符串形式并返回字符串。
        参数:
            value: 待渲染的字符串。
            params: 用于替换占位符的参数字典。
        返回:
            渲染后的字符串或其他类型值。
    """
    full_match = _TEMPLATE_RE.fullmatch(value.strip())
    if full_match:
        looked_up = _lookup(full_match.group(1), params=params)
        return "" if looked_up is _MISSING else looked_up

    def replace(match: re.Match[str]) -> str:
        """替换单个模板占位符。"""
        looked_up = _lookup(match.group(1), params=params)
        if looked_up is _MISSING:
            return ""
        if isinstance(looked_up, (dict, list)):
            return json.dumps(looked_up, ensure_ascii=False)
        return str(looked_up)

    return _TEMPLATE_RE.sub(replace, value)


def _lookup(expression: str, *, params: dict[str, Any]) -> Any:
    """解析表达式并在参数中查找值，最后应用可选过滤器。
        参数:
            expression: 形如 field.nested|json 的查找表达式。
            params: 参数树字典。
        返回:
            查找到的值，未找到时返回 _MISSING。
    """
    name, filters = _split_filters(expression)
    value = _dig(params, name.split("."))
    return _apply_filters(value, filters)


def _split_filters(expression: str) -> tuple[str, list[str]]:
    """将表达式按管道符拆分为字段名与过滤器列表。
        参数:
            expression: 查找表达式。
        返回:
            字段名与过滤器名称列表。
    """
    parts = [part.strip() for part in expression.split("|")]
    return parts[0], [part for part in parts[1:] if part]


def _apply_filters(value: Any, filters: list[str]) -> Any:
    """对值应用过滤器列表。
        当前支持 json 过滤器（序列化为 JSON 字符串）和 str 过滤器（转为字符串）。
        参数:
            value: 待过滤的值。
            filters: 过滤器名称列表。
        返回:
            过滤后的值。
    """
    if value is _MISSING:
        return value
    rendered = value
    for item in filters:
        if item == "json":
            rendered = json.dumps(rendered, ensure_ascii=False)
        elif item == "str":
            rendered = str(rendered)
        else:
            return _MISSING
    return rendered


def _parse_json(text: str) -> Any:
    """将文本解析为 JSON 对象。
        参数:
            text: 待解析的 JSON 字符串。
        返回:
            解析后的对象；解析失败时返回原始文本。
    """
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return text


def _dig(value: Any, parts: list[str]) -> Any:
    """按路径片段在嵌套结构中深入查找值。
        参数:
            value: 待查找的嵌套对象。
            parts: 以点为分隔的路径片段列表。
        返回:
            查找到的值，未找到时返回 _MISSING。
    """
    current = value
    if parts == [""]:
        return current
    for part in parts:
        if isinstance(current, dict):
            if part not in current:
                return _MISSING
            current = current[part]
        elif isinstance(current, list) and part.isdigit():
            index = int(part)
            if index >= len(current):
                return _MISSING
            current = current[index]
        else:
            return _MISSING
    return current


def _target_refs(value: Any, parts: list[str]) -> list[tuple[Any, Any, Any]]:
    """获取路径指向的所有目标引用。
        通配符 * 用于匹配列表中的每一项。
        参数:
            value: 待查找的嵌套对象。
            parts: 以点为分隔的路径片段列表。
        返回:
            (容器, 键, 值) 三元组列表。
    """
    if not parts or parts == [""]:
        return []
    part = parts[0]
    if len(parts) == 1:
        if part == "*" and isinstance(value, list):
            return [(value, index, item) for index, item in enumerate(value)]
        if isinstance(value, dict) and part in value:
            return [(value, part, value[part])]
        if isinstance(value, list) and part.isdigit():
            index = int(part)
            if index < len(value):
                return [(value, index, value[index])]
        return []

    next_values: list[Any] = []
    if part == "*" and isinstance(value, list):
        next_values.extend(value)
    elif isinstance(value, dict) and part in value:
        next_values.append(value[part])
    elif isinstance(value, list) and part.isdigit():
        index = int(part)
        if index < len(value):
            next_values.append(value[index])

    refs: list[tuple[Any, Any, Any]] = []
    for item in next_values:
        refs.extend(_target_refs(item, parts[1:]))
    return refs


def _deep_get(value: dict[str, Any], parts: list[str]) -> Any:
    """在字典结构中按路径片段获取深层值。
        参数:
            value: 字典对象。
            parts: 以点为分隔的键列表。
        返回:
            查找到的值，未找到时返回 _MISSING。
    """
    current: Any = value
    for part in parts:
        if not isinstance(current, dict) or part not in current:
            return _MISSING
        current = current[part]
    return current
