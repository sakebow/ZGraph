from __future__ import annotations

from typing import Any
from typing_extensions import TypedDict


class BaseState(TypedDict, total=False):
    """base状态。继承自 TypedDict。"""

    run_id: str
    created_at: str
    updated_at: str
    metadata: dict[str, Any]
