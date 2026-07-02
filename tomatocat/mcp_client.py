from __future__ import annotations

import asyncio
import json
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)


@dataclass
class MCPTool:
    name: str
    description: str = ""
    input_schema: dict[str, Any] = field(default_factory=dict)


class MCPServerConnection:
    def __init__(self, name: str, command: list[str], env: dict[str, str] | None = None):
        self.name = name
        self.command = command
        self.env = env
        self._proc: asyncio.subprocess.Process | None = None
        self._request_id = 0
        self._pending: dict[int, asyncio.Future] = {}
        self._read_task: asyncio.Task | None = None
        self._lock = asyncio.Lock()
        self._initialized = False

    async def start(self) -> None:
        full_env = dict(os.environ)
        if self.env:
            full_env.update(self.env)

        self._proc = await asyncio.create_subprocess_exec(
            *self.command,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=full_env,
        )

        self._read_task = asyncio.create_task(self._read_loop())
        await self._initialize()
        self._initialized = True
        log.info(f"[mcp] {self.name} 已启动")

    async def _read_loop(self) -> None:
        assert self._proc is not None
        assert self._proc.stdout is not None

        try:
            while True:
                line = await self._proc.stdout.readline()
                if not line:
                    break
                line_str = line.decode("utf-8").strip()
                if not line_str:
                    continue
                try:
                    msg = json.loads(line_str)
                except json.JSONDecodeError:
                    continue

                msg_id = msg.get("id")
                if msg_id is not None and msg_id in self._pending:
                    fut = self._pending.pop(msg_id)
                    if not fut.done():
                        if "result" in msg:
                            fut.set_result(msg["result"])
                        elif "error" in msg:
                            fut.set_exception(Exception(str(msg["error"])))
        except asyncio.CancelledError:
            pass
        except Exception as e:
            log.warning(f"[mcp] {self.name} 读取错误: {e}")

    async def _send_request(self, method: str, params: dict | None = None) -> Any:
        if self._proc is None or self._proc.stdin is None:
            raise RuntimeError(f"MCP server {self.name} 未启动")

        async with self._lock:
            self._request_id += 1
            req_id = self._request_id

        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        self._pending[req_id] = fut

        request = {
            "jsonrpc": "2.0",
            "id": req_id,
            "method": method,
        }
        if params is not None:
            request["params"] = params

        assert self._proc.stdin is not None
        self._proc.stdin.write((json.dumps(request) + "\n").encode("utf-8"))
        await self._proc.stdin.drain()

        try:
            return await asyncio.wait_for(fut, timeout=60)
        except asyncio.TimeoutError:
            self._pending.pop(req_id, None)
            raise TimeoutError(f"MCP 调用超时: {method}")

    async def _send_notification(self, method: str, params: dict | None = None) -> None:
        if self._proc is None or self._proc.stdin is None:
            return

        notification = {
            "jsonrpc": "2.0",
            "method": method,
        }
        if params is not None:
            notification["params"] = params

        assert self._proc.stdin is not None
        self._proc.stdin.write((json.dumps(notification) + "\n").encode("utf-8"))
        await self._proc.stdin.drain()

    async def _initialize(self) -> None:
        result = await self._send_request("initialize", {
            "protocolVersion": "2024-11-05",
            "capabilities": {
                "tools": {},
            },
            "clientInfo": {
                "name": "tomatocat",
                "version": "0.1.0",
            },
        })

        await self._send_notification("notifications/initialized")
        return result

    async def list_tools(self) -> list[MCPTool]:
        result = await self._send_request("tools/list")
        tools = []
        for t in result.get("tools", []):
            tools.append(MCPTool(
                name=t.get("name", ""),
                description=t.get("description", ""),
                input_schema=t.get("inputSchema", {}) or t.get("input_schema", {}) or {},
            ))
        return tools

    async def call_tool(self, tool_name: str, arguments: dict[str, Any]) -> str:
        result = await self._send_request("tools/call", {
            "name": tool_name,
            "arguments": arguments,
        })
        content = result.get("content", [])
        parts = []
        for c in content:
            if isinstance(c, dict) and "text" in c:
                parts.append(c["text"])
            elif hasattr(c, "text"):
                parts.append(c.text)
            else:
                parts.append(str(c))
        return "\n".join(parts)

    async def close(self) -> None:
        if self._read_task:
            self._read_task.cancel()
            try:
                await self._read_task
            except Exception:
                pass

        if self._proc:
            try:
                self._proc.terminate()
                await asyncio.wait_for(self._proc.wait(), timeout=5)
            except Exception:
                try:
                    self._proc.kill()
                except Exception:
                    pass
            self._proc = None

        for fut in self._pending.values():
            if not fut.done():
                fut.set_exception(RuntimeError("MCP connection closed"))
        self._pending.clear()
        self._initialized = False
        log.info(f"[mcp] {self.name} 已关闭")


class MCPClient:
    def __init__(self, workspace: Path, config_file: str = "mcp_servers.json"):
        self.workspace = workspace
        self.config_file = config_file
        self._servers: dict[str, MCPServerConnection] = {}
        self._tools: dict[str, MCPTool] = {}
        self._started = False

    async def start(self) -> list[MCPTool]:
        if self._started:
            return list(self._tools.values())

        config_path = self.workspace / self.config_file
        if not config_path.exists():
            log.info(f"[mcp] 配置文件不存在: {config_path}，跳过 MCP 初始化")
            return []

        with open(config_path, "r", encoding="utf-8-sig") as f:
            config = json.load(f)

        servers_cfg = config.get("servers", {})
        if not servers_cfg:
            log.info("[mcp] 没有配置 MCP 服务器")
            return []

        for name, server_cfg in servers_cfg.items():
            try:
                command = server_cfg.get("command", [])
                env = server_cfg.get("env")
                if not command:
                    continue

                conn = MCPServerConnection(name, command, env)
                await conn.start()
                self._servers[name] = conn

                tools = await conn.list_tools()
                for tool in tools:
                    tool_name = f"mcp_{name}__{tool.name}"
                    tool.name = tool_name
                    self._tools[tool_name] = tool

                log.info(f"[mcp] 已连接: {name} ({len(tools)} 个工具)")
            except Exception as e:
                log.warning(f"[mcp] 连接服务器 {name} 失败: {e}")

        self._started = True
        log.info(f"[mcp] 已连接 {len(self._servers)} 个服务器，共 {len(self._tools)} 个工具")
        return list(self._tools.values())

    async def call_tool(self, tool_name: str, arguments: dict[str, Any]) -> str:
        if tool_name not in self._tools:
            return f"错误: 工具 {tool_name} 不存在"

        if not tool_name.startswith("mcp_") or "__" not in tool_name:
            return f"错误: 工具名格式不正确: {tool_name}"

        server_name = tool_name[len("mcp_"):].split("__", 1)[0]
        inner_name = tool_name.split("__", 1)[1]
        server = self._servers.get(server_name)
        if not server:
            return f"错误: 服务器 {server_name} 未连接"

        try:
            return await server.call_tool(inner_name, arguments)
        except Exception as e:
            log.error(f"[mcp] 调用工具 {tool_name} 失败: {e}")
            return f"调用失败: {e}"

    async def close(self) -> None:
        for name, server in list(self._servers.items()):
            try:
                await server.close()
            except Exception:
                pass
        self._servers.clear()
        self._tools.clear()
        self._started = False
        log.info("[mcp] 所有连接已关闭")

    def get_tools(self) -> list[MCPTool]:
        return list(self._tools.values())
