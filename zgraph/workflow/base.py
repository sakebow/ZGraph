from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass(slots=True)
class WorkflowResult:
    name: str
    status: str
    data: dict[str, Any] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)


class Workflow(Protocol):
    name: str

    def run(self, state: dict[str, Any]) -> WorkflowResult:
        ...
