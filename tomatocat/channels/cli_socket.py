"""CLI Socket 渠道 - 通过 TCP Socket 与 CLI 客户端通信"""

from __future__ import annotations

import asyncio
import json
import logging

from .base import Channel

logger = logging.getLogger(__name__)


class CLISocketChannel(Channel):
    name = "cli"

    def __init__(self, host: str = "127.0.0.1", port: int = 8768) -> None:
        super().__init__()
        self.host = host
        self.port = port
        self._server: asyncio.Server | None = None

    async def start(self) -> None:
        self._server = await asyncio.start_server(
            self._handle_client,
            self.host,
            self.port,
        )
        logger.info("[cli] Socket 监听在 %s:%d", self.host, self.port)
        print(f"🍅🐱 CLI 连接地址: {self.host}:{self.port}")

    async def stop(self) -> None:
        if self._server:
            self._server.close()
            await self._server.wait_closed()

    async def _handle_client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        addr = writer.get_extra_info("peername")
        logger.info("[cli] 客户端连接: %s", addr)

        session_key = f"cli:{addr[0]}:{addr[1]}"

        try:
            while True:
                data = await reader.readline()
                if not data:
                    break

                text = data.decode("utf-8").strip()
                if not text:
                    continue

                try:
                    msg = json.loads(text)
                    text = msg.get("text", text)
                except json.JSONDecodeError:
                    pass

                response = await self._handle_message(session_key, text, "cli")

                response_data = json.dumps({
                    "type": "text",
                    "content": response,
                }, ensure_ascii=False)
                writer.write((response_data + "\n").encode("utf-8"))
                await writer.drain()

        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error("[cli] 客户端处理错误: %s", e)
        finally:
            writer.close()
            await writer.wait_closed()
            logger.info("[cli] 客户端断开: %s", addr)
