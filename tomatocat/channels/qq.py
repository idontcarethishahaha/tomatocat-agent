"""QQ 渠道 - 通过 NapCat OneBot WebSocket 接入

消息流向：QQ → NapCat → WebSocket → 番茄猫 → WebSocket → NapCat → QQ

chat_id 约定：
  私聊："{user_id}"           （如 "987654321"）
  群聊："gqq:{group_id}"     （如 "gqq:111222333"）
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import re
from pathlib import Path
from typing import Any

from .base import Channel

logger = logging.getLogger(__name__)

_GROUP_PREFIX = "gqq:"
_CQ_IMAGE_RE = re.compile(r"\[CQ:image[^\]]*?(?:,|\b)url=([^,\]]+)[^\]]*\]")


class QQChannel(Channel):
    name = "qq"

    def __init__(
        self,
        ws_url: str = "ws://localhost:3001",
        bot_uin: str = "",
        allow_from: list[str] | None = None,
        groups: list[str] | None = None,
    ) -> None:
        super().__init__()
        self.ws_url = ws_url
        self.bot_uin = bot_uin
        self.allow_from = set(str(u) for u in (allow_from or []))
        self.groups = set(str(g) for g in (groups or []))
        self._ws: Any = None
        self._task: asyncio.Task | None = None
        self._running = False
        self._msg_id = 0
        self._pending_msgs: dict[int, asyncio.Future] = {}

    async def start(self) -> None:
        if not self.ws_url:
            logger.info("[qq] 未配置 WebSocket 地址，跳过")
            return

        self._running = True
        self._task = asyncio.create_task(self._connect_loop())
        logger.info("[qq] QQ 渠道正在连接 %s ...", self.ws_url)

    async def stop(self) -> None:
        self._running = False
        if self._ws:
            try:
                await self._ws.close()
            except Exception:
                pass
            self._ws = None
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        logger.info("[qq] QQ 渠道已停止")

    # ── 连接管理 ──────────────────────────────────────────────────

    async def _connect_loop(self) -> None:
        """持续重连的循环"""
        import websockets

        while self._running:
            try:
                async with websockets.connect(self.ws_url) as ws:
                    self._ws = ws
                    logger.info("[qq] 已连接到 NapCat OneBot")
                    print("渠道已启动: qq")

                    async for raw_msg in ws:
                        try:
                            await self._handle_ws_message(raw_msg)
                        except Exception as e:
                            logger.error("[qq] 消息处理失败: %s", e)
            except Exception as e:
                logger.warning("[qq] 连接断开，5秒后重连: %s", e)
                self._ws = None
                if self._running:
                    await asyncio.sleep(5)

    async def _handle_ws_message(self, raw: str) -> None:
        """处理收到的 WebSocket 消息"""
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return

        # 如果是 API 响应（有 echo 字段）
        if "echo" in data:
            echo_id = data.get("echo")
            if isinstance(echo_id, int) and echo_id in self._pending_msgs:
                fut = self._pending_msgs.pop(echo_id)
                if not fut.done():
                    fut.set_result(data.get("data"))
                return

        # 如果是事件消息
        post_type = data.get("post_type")
        if post_type == "message":
            await self._handle_message_event(data)

    async def _handle_message_event(self, data: dict[str, Any]) -> None:
        """处理消息事件"""
        msg_type = data.get("message_type")
        user_id = str(data.get("user_id", ""))
        raw_message = data.get("raw_message", "")
        message_id = data.get("message_id", 0)

        if msg_type == "private":
            # 私聊
            if self.allow_from and user_id not in self.allow_from:
                logger.warning("[qq] 拒绝未授权私聊: %s", user_id)
                return

            text, img_urls = self._extract_cq_images(raw_message)
            preview = text[:60] + "..." if len(text) > 60 else text
            logger.info("[qq] 私聊消息  user=%s  内容: %s", user_id, preview)

            result = await self._handle_message(f"qq:{user_id}", text, "qq")
            reply_text = result.get("text", "")
            media_paths = result.get("media_paths", [])
            if reply_text:
                await self._send_private_text(user_id, reply_text)
            for media_path in media_paths:
                try:
                    await self.send_image(user_id, str(media_path))
                except Exception as e:
                    logger.error("[qq] meme 图片发送失败: %s", e)

        elif msg_type == "group":
            # 群聊
            group_id = str(data.get("group_id", ""))
            if self.groups and group_id not in self.groups:
                return

            # 只响应 @ 机器人的消息
            at_self = f"[CQ:at,qq={self.bot_uin}]" in raw_message
            if not at_self:
                # 如果没有配置白名单，只响应 @ 消息
                if self.groups:
                    pass  # 配置了群白名单，都处理
                else:
                    return

            # 去掉 @ 机器人
            text = raw_message
            text = re.sub(rf"\[CQ:at,qq={self.bot_uin}[^\]]*\]", "", text).strip()
            text, img_urls = self._extract_cq_images(text)

            if not text.strip():
                return

            chat_id = f"{_GROUP_PREFIX}{group_id}"
            session_key = f"qq:{chat_id}"
            preview = text[:60] + "..." if len(text) > 60 else text
            logger.info("[qq] 群聊消息  group=%s  user=%s  内容: %s", group_id, user_id, preview)

            response = await self._handle_message(session_key, text, "qq")
            reply_text = response.get("text", "")
            media_paths = response.get("media_paths", [])
            if reply_text:
                reply_with_at = f"[CQ:at,qq={user_id}]\n{reply_text}"
                await self._send_group_text(group_id, reply_with_at)
            for media_path in media_paths:
                try:
                    await self.send_image(f"{_GROUP_PREFIX}{group_id}", str(media_path))
                except Exception as e:
                    logger.error("[qq] meme 图片发送失败: %s", e)

    # ── 发送消息 API ─────────────────────────────────────────────

    async def send_message(self, chat_id: str, text: str) -> None:
        """发送文本消息（自动区分私聊/群聊）"""
        if chat_id.startswith(_GROUP_PREFIX):
            group_id = chat_id[len(_GROUP_PREFIX):]
            await self._send_group_text(group_id, text)
        else:
            await self._send_private_text(chat_id, text)

    async def send_image(self, chat_id: str, image_path: str) -> None:
        """发送图片"""
        if chat_id.startswith(_GROUP_PREFIX):
            group_id = chat_id[len(_GROUP_PREFIX):]
            await self._send_group_image(group_id, image_path)
        else:
            await self._send_private_image(chat_id, image_path)

    async def _send_private_text(self, user_id: str, text: str) -> None:
        await self._call_api("send_private_msg", {
            "user_id": int(user_id),
            "message": text,
        })

    async def _send_group_text(self, group_id: str, text: str) -> None:
        await self._call_api("send_group_msg", {
            "group_id": int(group_id),
            "message": text,
        })

    async def _send_private_image(self, user_id: str, image_path: str) -> None:
        cq_img = self._path_to_cq_image(image_path)
        await self._call_api("send_private_msg", {
            "user_id": int(user_id),
            "message": cq_img,
        })

    async def _send_group_image(self, group_id: str, image_path: str) -> None:
        cq_img = self._path_to_cq_image(image_path)
        await self._call_api("send_group_msg", {
            "group_id": int(group_id),
            "message": cq_img,
        })

    async def _call_api(self, action: str, params: dict[str, Any]) -> Any:
        """调用 OneBot API"""
        if not self._ws:
            logger.warning("[qq] WebSocket 未连接，无法调用 API: %s", action)
            return None

        self._msg_id += 1
        msg_id = self._msg_id
        payload = {
            "action": action,
            "params": params,
            "echo": msg_id,
        }

        loop = asyncio.get_running_loop()
        fut = loop.create_future()
        self._pending_msgs[msg_id] = fut

        try:
            await self._ws.send(json.dumps(payload, ensure_ascii=False))
            return await asyncio.wait_for(fut, timeout=10)
        except asyncio.TimeoutError:
            logger.warning("[qq] API 调用超时: %s", action)
            return None
        except Exception as e:
            logger.warning("[qq] API 调用失败 %s: %s", action, e)
            return None
        finally:
            self._pending_msgs.pop(msg_id, None)

    # ── 工具函数 ─────────────────────────────────────────────────

    def _extract_cq_images(self, raw: str) -> tuple[str, list[str]]:
        """从 CQ 码中提取图片 URL，返回 (纯文本, [url...])"""
        urls = _CQ_IMAGE_RE.findall(raw)
        text = re.sub(r"\[CQ:image[^\]]*\]", "", raw).strip()
        return text, urls

    def _path_to_cq_image(self, path: str) -> str:
        """本地图片路径转 CQ 码"""
        if path.startswith(("http://", "https://")):
            return f"[CQ:image,file={path}]"
        # 本地文件，转 base64
        try:
            data = Path(path).read_bytes()
            b64 = base64.b64encode(data).decode()
            return f"[CQ:image,file=base64://{b64}]"
        except Exception as e:
            logger.warning("[qq] 图片读取失败: %s", e)
            return f"[CQ:image,file={path}]"
