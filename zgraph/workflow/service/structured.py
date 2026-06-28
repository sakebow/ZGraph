from __future__ import annotations

import json
from typing import Any, TypeVar

from pydantic import BaseModel


T = TypeVar("T", bound=BaseModel)


def coerce_model_output(result: Any, schema: type[T]) -> T:
    if isinstance(result, schema):
        return result
    if isinstance(result, dict):
        return schema.model_validate(result)

    content = getattr(result, "content", result)
    if isinstance(content, dict):
        return schema.model_validate(content)
    if isinstance(content, list):
        content = " ".join(str(item.get("text", item)) if isinstance(item, dict) else str(item) for item in content)

    if not isinstance(content, str):
        raise ValueError(f"Unsupported structured model output: {type(result)!r}")

    return schema.model_validate(json.loads(extract_json_object(content)))


def extract_json_object(text: str) -> str:
    starts = [index for index, char in enumerate(text) if char == "{"]
    for start in starts:
        candidate = _scan_json_object(text, start)
        if candidate:
            return candidate
    raise ValueError("No JSON object found in model output")


def _scan_json_object(text: str, start: int) -> str:
    depth = 0
    in_string = False
    escape = False
    for index in range(start, len(text)):
        char = text[index]
        if in_string:
            if escape:
                escape = False
            elif char == "\\":
                escape = True
            elif char == '"':
                in_string = False
            continue

        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[start : index + 1]
    return ""
