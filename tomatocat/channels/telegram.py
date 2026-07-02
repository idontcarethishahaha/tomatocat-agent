"""Telegram 渠道"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from telegram import Update
from telegram.error import TimedOut, NetworkError, RetryAfter
from telegram.ext import (
    ApplicationBuilder,
    ContextTypes,
    MessageHandler,
    filters,
    CommandHandler,
)

from .base import Channel

logger = logging.getLogger(__name__)

# 主动消息发送的超时和重试配置
_SEND_TIMEOUT = 30  # 秒
_SEND_RETRIES = 2
_SEND_RETRY_DELAY = 3  # 秒


class TelegramChannel(Channel):
    name = "telegram"

    def __init__(self, token: str, allow_from: list[str] | None = None) -> None:
        super().__init__()
        self.token = token
        self.allow_from = allow_from or []
        self._application: Any = None

    async def start(self) -> None:
        if not self.token:
            logger.info("[telegram] 未配置 token，跳过")
            return

        self._application = (
            ApplicationBuilder()
            .token(self.token)
            .build()
        )

        self._application.add_handler(CommandHandler("start", self._start_cmd))
        self._application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self._on_message))

        await self._application.initialize()
        await self._application.start()
        await self._application.updater.start_polling()
        logger.info("[telegram] Telegram 渠道已启动")
        print("渠道已启动: telegram")

    async def stop(self) -> None:
        if self._application:
            await self._application.updater.stop()
            await self._application.stop()
            await self._application.shutdown()

    async def send_message(self, chat_id: str, text: str) -> None:
        """发送主动消息，带超时和重试"""
        if not self._application or not chat_id:
            return

        # 去掉 chat_id 中的 channel 前缀（如 "telegram:8824997206" → "8824997206"）
        if ":" in str(chat_id):
            chat_id = str(chat_id).split(":", 1)[1]

        for attempt in range(_SEND_RETRIES + 1):
            try:
                await asyncio.wait_for(
                    self._application.bot.send_message(
                        chat_id=chat_id,
                        text=text,
                        disable_web_page_preview=True,
                    ),
                    timeout=_SEND_TIMEOUT,
                )
                logger.info(f"[telegram] 主动消息已发送至 {chat_id}")
                return
            except TimedOut:
                logger.warning(
                    f"[telegram] 主动消息发送超时 (attempt {attempt + 1}/{_SEND_RETRIES + 1}) chat_id={chat_id}"
                )
                if attempt < _SEND_RETRIES:
                    await asyncio.sleep(_SEND_RETRY_DELAY)
                else:
                    logger.error(f"[telegram] 主动消息发送失败（超时重试耗尽）: chat_id={chat_id}")
            except RetryAfter as e:
                logger.warning(f"[telegram] 触发限流，等待 {e.retry_after}s 后重试")
                await asyncio.sleep(e.retry_after)
            except NetworkError as e:
                logger.warning(
                    f"[telegram] 网络错误 (attempt {attempt + 1}): {e}"
                )
                if attempt < _SEND_RETRIES:
                    await asyncio.sleep(_SEND_RETRY_DELAY)
                else:
                    logger.error(f"[telegram] 主动消息发送失败（网络错误重试耗尽）: {e}")
            except Exception as e:
                logger.error(f"[telegram] 主动消息发送失败: {e}")
                return

    async def send_photo(self, chat_id: str, photo_path: str, caption: str = "") -> None:
        if not self._application or not chat_id:
            return
        try:
            with open(photo_path, "rb") as f:
                await self._application.bot.send_photo(
                    chat_id=chat_id,
                    photo=f,
                    caption=caption,
                )
        except Exception as e:
            logger.error(f"[telegram] 图片发送失败: {e}")

    async def send_animation(self, chat_id: str, gif_path: str, caption: str = "") -> None:
        if not self._application or not chat_id:
            return
        try:
            with open(gif_path, "rb") as f:
                await self._application.bot.send_animation(
                    chat_id=chat_id,
                    animation=f,
                    caption=caption,
                )
        except Exception as e:
            logger.error(f"[telegram] GIF发送失败: {e}")

    def _is_allowed(self, username: str | None) -> bool:
        if not self.allow_from:
            return True
        return username in self.allow_from

    async def _start_cmd(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        user = update.effective_user
        if not user:
            return
        if not self._is_allowed(user.username):
            await update.message.reply_text("你没有权限使用番茄猫哦 (・_・;)")
            return
        await update.message.reply_text("喵~ 番茄猫已上线！有什么可以帮你的吗？(=^･ω･^=)")

    async def _on_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        user = update.effective_user
        if not user or not update.message or not update.message.text:
            return

        if not self._is_allowed(user.username):
            await update.message.reply_text("你没有权限使用番茄猫哦 (・_・;)")
            return

        session_key = f"telegram:{user.id}"
        text = update.message.text

        try:
            result = await self._handle_message(session_key, text, "telegram")
            reply_text = result.get("text", "")
            media_paths = result.get("media_paths", [])

            # 先发文本
            if reply_text.strip():
                await update.message.reply_text(reply_text)

            # 再发 meme 媒体文件
            for media_path in media_paths:
                path_str = str(media_path)
                try:
                    if path_str.lower().endswith(".gif"):
                        await self.send_animation(str(user.id), path_str)
                    else:
                        await self.send_photo(str(user.id), path_str)
                except Exception as e:
                    logger.error("[telegram] meme 图片发送失败: %s", e)
        except Exception as e:
            logger.error("[telegram] 消息处理失败: %s", e)
            await update.message.reply_text("喵... 番茄猫出错了，等一下再试试？(・_・;)")
