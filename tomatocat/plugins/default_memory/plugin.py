from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Protocol, runtime_checkable

from tomatocat.core.memory.engine import MemoryEngine

logger = logging.getLogger(__name__)


@runtime_checkable
class MemoryPlugin(Protocol):
    plugin_id: str

    def build(self, deps: MemoryPluginBuildDeps) -> MemoryPluginRuntime: ...


@dataclass(frozen=True)
class MemoryPluginBuildDeps:
    workspace: Path
    config: object
    llm_provider: object
    event_bus: object


@dataclass
class MemoryPluginRuntime:
    engine: MemoryEngine
    closeables: list[object] = field(default_factory=list)


class DefaultMemoryPlugin:
    plugin_id = "default_memory"

    def build(self, deps: MemoryPluginBuildDeps) -> MemoryPluginRuntime:
        from .engine import DefaultMemoryEngine

        engine = DefaultMemoryEngine(deps.workspace, deps.config, deps.llm_provider, deps.event_bus)
        return MemoryPluginRuntime(engine=engine)