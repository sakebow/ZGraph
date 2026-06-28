"""ZGraph agent runtime.

The package exposes a small but complete runtime that can be used from the
CLI, an OpenAI-compatible HTTP endpoint, or embedded Python code.
"""

from zgraph.config import Settings
from zgraph.runtime import ZGraphRuntime

__all__ = ["Settings", "ZGraphRuntime"]

