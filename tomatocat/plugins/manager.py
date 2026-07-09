"""插件管理器：加载、管理和执行插件"""

from __future__ import annotations

import importlib
import importlib.util
import logging
import sys
from pathlib import Path
from typing import Any

from .base import Plugin, PluginContext
from .decorators import ToolInfo, _tool_registry, get_tool_definition

logger = logging.getLogger(__name__)


class PluginManager:
    def __init__(
        self,
        plugins_dir: Path,
        workspace: Path,
        event_bus: Any,
        session_manager: Any,
        memory: Any = None,
    ) -> None:
        self.plugins_dir = Path(plugins_dir)
        self.workspace = Path(workspace)
        self.event_bus = event_bus
        self.session_manager = session_manager
        self.memory = memory
        self.proactive = None
        self._plugins: dict[str, Plugin] = {}
        self._tools: dict[str, ToolInfo] = {}
        # 共享 context，供插件之间共享 subagent_manager、策略等资源
        self.context: dict[str, Any] = {}

    async def load_all(self) -> None:
        if not self.plugins_dir.exists():
            logger.info("插件目录不存在: %s", self.plugins_dir)
            return

        for plugin_dir in sorted(self.plugins_dir.iterdir()):
            if not plugin_dir.is_dir():
                continue
            plugin_file = plugin_dir / "plugin.py"
            if not plugin_file.exists():
                continue
            try:
                await self._load_plugin(plugin_dir, plugin_file)
            except Exception as e:
                logger.error("加载插件失败 %s: %s", plugin_dir.name, e)

    async def _load_plugin(self, plugin_dir: Path, plugin_file: Path) -> None:
        plugin_id = plugin_dir.name

        module_name = f"tomatocat_plugin_{plugin_id}"
        spec = importlib.util.spec_from_file_location(module_name, plugin_file)
        if spec is None or spec.loader is None:
            raise ImportError(f"无法加载插件模块: {plugin_file}")

        before_count = len(_tool_registry)
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        spec.loader.exec_module(module)

        plugin_class = None
        for attr_name in dir(module):
            attr = getattr(module, attr_name)
            if (
                isinstance(attr, type)
                and issubclass(attr, Plugin)
                and attr is not Plugin
            ):
                plugin_class = attr
                break

        if plugin_class is None:
            raise ValueError(f"插件中未找到 Plugin 子类: {plugin_file}")

        plugin_instance = plugin_class()
        plugin_instance.name = plugin_instance.name or plugin_id
        plugin_instance.context = PluginContext(
            plugin_id=plugin_id,
            plugin_dir=plugin_dir,
            workspace=self.workspace,
            manager=self,
        )

        await plugin_instance.initialize()
        self._plugins[plugin_id] = plugin_instance

        new_tools = _tool_registry[before_count:]
        for tool_info in new_tools:
            tool_info.plugin_id = plugin_id
            self._tools[tool_info.name] = tool_info
            logger.info("[plugin] 工具已注册: %s (来自 %s)", tool_info.name, plugin_id)

        logger.info("[plugin] 插件已加载: %s", plugin_id)

    async def unload_all(self) -> None:
        for plugin_id, plugin in list(self._plugins.items()):
            try:
                await plugin.terminate()
            except Exception as e:
                logger.error("插件卸载失败 %s: %s", plugin_id, e)
        self._plugins.clear()
        self._tools.clear()

    def get_tool(self, tool_name: str) -> ToolInfo | None:
        return self._tools.get(tool_name)

    def get_all_tools(self) -> list[dict[str, Any]]:
        return [get_tool_definition(info) for info in self._tools.values()]

    async def execute_tool(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        *,
        session_key: str = "",
        channel: str = "",
    ) -> str:
        tool_info = self._tools.get(tool_name)
        if tool_info is None:
            return f"错误：未找到工具 '{tool_name}'"

        call_kwargs = dict(arguments)
        if session_key:
            call_kwargs["_session_key"] = session_key
        if channel:
            call_kwargs["_channel"] = channel

        # MCP 工具（plugin_id="mcp"）直接调用，不需要插件实例
        if tool_info.plugin_id == "mcp":
            try:
                result = tool_info.func(None, **call_kwargs)
                if hasattr(result, "__await__"):
                    result = await result
                return str(result) if result is not None else ""
            except Exception as e:
                logger.error("MCP工具执行失败 %s: %s", tool_name, e)
                return f"工具执行出错: {e}"

        plugin = self._plugins.get(tool_info.plugin_id or "")
        if plugin is None:
            return f"错误：工具 '{tool_name}' 所属插件未找到"

        try:
            result = tool_info.func(plugin, None, **call_kwargs)
            if hasattr(result, "__await__"):
                result = await result
            return str(result) if result is not None else ""
        except Exception as e:
            logger.error("工具执行失败 %s: %s", tool_name, e)
            return f"工具执行出错: {e}"

    def register_mcp_tools(self, mcp_client: Any) -> None:
        from .decorators import ToolInfo

        tools = mcp_client.get_tools()
        for mcp_tool in tools:
            tool_name = mcp_tool.name

            async def _mcp_handler(event: object, _tn: str = tool_name, **kwargs: Any) -> str:
                return await mcp_client.call_tool(_tn, kwargs)

            tool_info = ToolInfo(
                name=tool_name,
                description=mcp_tool.description or "",
                func=_mcp_handler,
                parameters=mcp_tool.input_schema or {"type": "object", "properties": {}},
                plugin_id="mcp",
            )
            self._tools[tool_name] = tool_info
            logger.info("[plugin] MCP工具已注册: %s", tool_name)

    @property
    def plugins(self) -> dict[str, Plugin]:
        return self._plugins
