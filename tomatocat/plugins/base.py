"""插件基类"""

from __future__ import annotations

from abc import ABC
from pathlib import Path
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from .manager import PluginManager


class PluginContext:
    """插件上下文：插件可以访问的资源"""

    def __init__(
        self,
        plugin_id: str,
        plugin_dir: Path,
        workspace: Path,
        manager: PluginManager,
    ) -> None:
        self.plugin_id = plugin_id
        self.plugin_dir = plugin_dir
        self.workspace = workspace
        self._manager = manager

    @property
    def event_bus(self) -> Any:
        return self._manager.event_bus

    @property
    def session_manager(self) -> Any:
        return self._manager.session_manager

    @property
    def memory(self) -> Any:
        return self._manager.memory


class Plugin(ABC):
    """所有插件的基类"""

    name: str | None = None
    desc: str | None = None
    version: str = "0.1.0"

    context: PluginContext

    def __init__(self) -> None:
        pass

    async def initialize(self) -> None:
        """插件初始化时调用"""
        pass

    async def terminate(self) -> None:
        """插件卸载时调用"""
        pass
