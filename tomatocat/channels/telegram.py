"""Telegram 渠道（流式思考 + 可折叠思考块 + 工具代码块 + 图片分析）"""

from __future__ import annotations

import asyncio
import html as _html
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from telegram import MessageEntity, Update
from telegram.error import TimedOut, NetworkError, RetryAfter, RetryAfter as _RetryAfter
from telegram.ext import (
    ApplicationBuilder,
    ContextTypes,
    MessageHandler,
    filters,
    CommandHandler,
)

from .base import Channel

logger = logging.getLogger(__name__)

_SEND_TIMEOUT = 30
_SEND_RETRIES = 2
_SEND_RETRY_DELAY = 3

_LIVE_MIN_INTERVAL_S = 1.5
_LIVE_MIN_CHARS = 60
_THINKING_TAIL = 1400
_TOOL_MAX_LINES = 12
_REPLY_TAIL = 900

_TOOL_EMOJI: dict[str, str] = {
    "schedule": "🔔",
    "set_schedule": "🔔",
    "study_plan": "📚",
    "web_search": "🔍",
    "web_fetch": "📄",
    "memory": "🧠",
    "shell": "💻",
    "filesystem": "📁",
    "list_dir": "📁",
    "read_file": "📄",
    "write_file": "✏️",
    "edit_file": "✏️",
    "record_expense": "💰",
    "expense_stat": "📊",
    "get_study_progress": "📚",
    "create_study_plan": "📝",
    "mcp_": "🔗",
}


def _tool_emoji(name: str) -> str:
    for prefix, emoji in _TOOL_EMOJI.items():
        if name.startswith(prefix):
            return emoji
    return "🔧"


def _utf16_len(text: str) -> int:
    """计算 UTF-16 code units 数量（Telegram entities offset/length 使用）。"""
    return len(text.encode("utf-16-le")) // 2


@dataclass
class _LiveState:
    chat_id: int = 0
    message_id: int = 0
    thinking_buf: str = ""
    reply_buf: str = ""
    tool_lines: list[dict[str, Any]] = field(default_factory=list)
    next_update_at: float = 0.0
    last_len: int = 0
    update_task: asyncio.Task | None = None
    dirty: bool = False
    final_sent: bool = False


class TelegramChannel(Channel):
    name = "telegram"

    def __init__(
        self,
        token: str,
        allow_from: list[str] | None = None,
        upload_dir: Path | None = None,
    ) -> None:
        super().__init__()
        self.token = token
        self.allow_from = allow_from or []
        self._application: Any = None
        self._live: dict[str, _LiveState] = {}
        self._upload_dir = upload_dir or Path(".")

    async def start(self) -> None:
        if not self.token:
            logger.info("[telegram] 未配置 token，跳过")
            return

        self._upload_dir.mkdir(parents=True, exist_ok=True)

        self._application = (
            ApplicationBuilder()
            .token(self.token)
            .build()
        )

        self._application.add_handler(CommandHandler("start", self._start_cmd))
        self._application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self._on_message))
        self._application.add_handler(MessageHandler(filters.PHOTO, self._on_photo))

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

    async def _on_photo(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """处理图片消息（下载最高分辨率图片，传给 agent 分析）"""
        user = update.effective_user
        if not user or not update.message:
            return

        if not self._is_allowed(user.username):
            await update.message.reply_text("你没有权限使用番茄猫哦 (・_・;)")
            return

        if not update.message.photo:
            return

        session_key = f"telegram:{user.id}"
        chat_id = user.id

        # 下载最高分辨率的图片
        photo = max(update.message.photo, key=lambda p: p.file_size or 0)
        try:
            file = await context.bot.get_file(photo.file_id)
            import time
            filename = f"photo_{user.id}_{int(time.time() * 1000)}.jpg"
            save_path = self._upload_dir / filename
            await file.download_to_drive(save_path)
            logger.info(f"[telegram] 收到图片  chat_id={chat_id}  path={save_path}")
        except Exception as e:
            logger.error(f"[telegram] 图片下载失败: {e}")
            await update.message.reply_text("喵... 图片下载失败了，再试一次？(・_・;)")
            return

        caption = update.message.caption or "请分析这张图片"

        await self._process_message(
            session_key=session_key,
            text=caption,
            chat_id=chat_id,
            update=update,
            media_paths=[str(save_path)],
        )

    async def _on_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        user = update.effective_user
        if not user or not update.message or not update.message.text:
            return

        if not self._is_allowed(user.username):
            await update.message.reply_text("你没有权限使用番茄猫哦 (・_・;)")
            return

        session_key = f"telegram:{user.id}"
        text = update.message.text
        chat_id = user.id

        await self._process_message(
            session_key=session_key,
            text=text,
            chat_id=chat_id,
            update=update,
            media_paths=None,
        )

    async def _process_message(
        self,
        session_key: str,
        text: str,
        chat_id: int,
        update: Update,
        media_paths: list[str] | None,
    ) -> None:
        """统一的消息处理流程（文本和图片共用）"""
        state = _LiveState(chat_id=chat_id)
        self._live[session_key] = state

        async def _on_delta(
            channel: str,
            sess_key: str,
            delta_type: str,
            data: dict[str, Any],
        ) -> None:
            if delta_type == "streaming_delta":
                if "thinking_delta" in data:
                    state.thinking_buf += data["thinking_delta"]
                if "content_delta" in data:
                    state.reply_buf += data["content_delta"]
                state.dirty = True
                self._schedule_live_update(session_key, state)
            elif delta_type == "tool_call_start":
                state.tool_lines.append(data)
                state.dirty = True
                self._schedule_live_update(session_key, state)
            elif delta_type == "tool_call_done":
                for line in state.tool_lines:
                    if line.get("name") == data.get("name") and line.get("status") == "running":
                        line["status"] = "done"
                        line["result_preview"] = data.get("result_preview", "")
                        break
                state.dirty = True
                self._schedule_live_update(session_key, state)

        try:
            kwargs: dict[str, Any] = {"on_delta": _on_delta}
            if media_paths:
                kwargs["media_paths"] = media_paths

            result = await self._handle_message(session_key, text, "telegram", **kwargs)
            reply_text = result.get("text", "")
            result_media = result.get("media_paths", [])
            thinking = result.get("thinking", "")
            tool_calls = result.get("tool_calls", [])

            # 标记完成，取消实时更新
            state.final_sent = True
            self._cancel_live_update(state)

            # 删除流式预览消息
            if state.message_id != 0:
                try:
                    await self._application.bot.delete_message(
                        chat_id=state.chat_id,
                        message_id=state.message_id,
                    )
                except Exception:
                    pass

            # 1. 发送思考过程（可折叠块引用）
            if thinking and thinking.strip():
                await self._send_thinking_block(chat_id, thinking)

            # 2. 发送工具调用（代码块，带复制功能）
            if tool_calls or state.tool_lines:
                tool_text = _format_tool_final(state.tool_lines)
                if tool_text:
                    await self._send_tool_block(chat_id, tool_text)

            # 3. 发送最终回复（纯文本，meme 等会自动在 channel 层处理）
            if reply_text.strip():
                await update.message.reply_text(reply_text)

            # 发送 meme 等媒体
            for media_path in result_media:
                path_str = str(media_path)
                try:
                    if path_str.lower().endswith(".gif"):
                        await self.send_animation(str(chat_id), path_str)
                    else:
                        await self.send_photo(str(chat_id), path_str)
                except Exception as e:
                    logger.error("[telegram] 媒体发送失败: %s", e)

        except Exception as e:
            logger.error("[telegram] 消息处理失败: %s", e)
            await update.message.reply_text("喵... 番茄猫出错了，等一下再试试？(・_・;)")
        finally:
            self._live.pop(session_key, None)

    async def _send_thinking_block(self, chat_id: int, thinking: str) -> None:
        """发送可折叠的思考过程块（expandable_blockquote）。"""
        header = "💭 思考过程\n\n"
        max_utf16 = 4080
        header_utf16 = _utf16_len(header)

        chunks = _split_by_utf16(thinking, max_utf16 - header_utf16)
        for i, chunk in enumerate(chunks):
            text = (header if i == 0 else "") + chunk
            utf16_len = _utf16_len(text)
            entity = MessageEntity(
                type="expandable_blockquote",
                offset=0,
                length=utf16_len,
            )
            try:
                await self._application.bot.send_message(
                    chat_id=chat_id,
                    text=text,
                    entities=[entity],
                    disable_web_page_preview=True,
                )
            except Exception as e:
                logger.warning("[telegram] 思考块发送失败 chunk %d: %s", i, e)
                # 降级为普通文本
                try:
                    await self._application.bot.send_message(
                        chat_id=chat_id,
                        text=text,
                        disable_web_page_preview=True,
                    )
                except Exception:
                    pass
                return

    async def _send_tool_block(self, chat_id: int, tool_text: str) -> None:
        """发送工具调用（Markdown 代码块，Telegram 自带复制按钮）。"""
        text = f"```\n{tool_text}\n```"
        try:
            await self._application.bot.send_message(
                chat_id=chat_id,
                text=text,
                parse_mode="Markdown",
                disable_web_page_preview=True,
            )
        except Exception as e:
            logger.warning("[telegram] 工具块 Markdown 发送失败，降级纯文本: %s", e)
            try:
                await self._application.bot.send_message(
                    chat_id=chat_id,
                    text=tool_text,
                    disable_web_page_preview=True,
                )
            except Exception:
                pass

    def _schedule_live_update(self, session_key: str, state: _LiveState) -> None:
        loop = asyncio.get_running_loop()
        now = loop.time()
        if now < state.next_update_at:
            current_len = len(state.thinking_buf) + len(state.reply_buf) + len(state.tool_lines) * 20
            if current_len - state.last_len < _LIVE_MIN_CHARS:
                return

        if state.update_task and not state.update_task.done():
            return

        state.next_update_at = now + _LIVE_MIN_INTERVAL_S
        state.last_len = len(state.thinking_buf) + len(state.reply_buf) + len(state.tool_lines) * 20
        state.update_task = loop.create_task(self._debounced_update(session_key, state))

    async def _debounced_update(self, session_key: str, state: _LiveState) -> None:
        try:
            await self._do_live_update(session_key, state, terminal=False)
        except Exception as e:
            logger.warning("[telegram] 实时更新失败: %s", e)

    def _cancel_live_update(self, state: _LiveState) -> None:
        if state.update_task and not state.update_task.done():
            state.update_task.cancel()
            state.update_task = None

    async def _do_live_update(self, session_key: str, state: _LiveState, *, terminal: bool) -> None:
        if not self._application:
            return

        text_html = _format_live_html(
            thinking=state.thinking_buf,
            tool_lines=state.tool_lines,
            reply=state.reply_buf,
            terminal=terminal,
        )

        if not text_html.strip():
            return

        if state.message_id == 0:
            try:
                msg = await self._application.bot.send_message(
                    chat_id=state.chat_id,
                    text=text_html,
                    parse_mode="HTML",
                    disable_web_page_preview=True,
                )
                state.message_id = msg.message_id
                state.dirty = False
            except Exception as e:
                logger.warning("[telegram] 实时消息发送失败，尝试纯文本: %s", e)
                try:
                    plain = _format_live_plain(
                        thinking=state.thinking_buf,
                        tool_lines=state.tool_lines,
                        reply=state.reply_buf,
                        terminal=terminal,
                    )
                    msg = await self._application.bot.send_message(
                        chat_id=state.chat_id,
                        text=plain,
                        disable_web_page_preview=True,
                    )
                    state.message_id = msg.message_id
                    state.dirty = False
                except Exception as e2:
                    logger.warning("[telegram] 纯文本也失败: %s", e2)
            return

        if not state.dirty and not terminal:
            return

        try:
            await self._application.bot.edit_message_text(
                chat_id=state.chat_id,
                message_id=state.message_id,
                text=text_html,
                parse_mode="HTML",
                disable_web_page_preview=True,
            )
            state.dirty = False
        except Exception as e:
            err_str = str(e)
            if "not modified" in err_str.lower():
                state.dirty = False
                return
            logger.debug("[telegram] 实时消息编辑跳过: %s", e)


def _tail(text: str, max_len: int) -> str:
    if len(text) <= max_len:
        return text
    return "..." + text[-max_len:]


def _split_by_utf16(text: str, max_utf16: int) -> list[str]:
    """按行切分文本，每段不超过 max_utf16 个 UTF-16 code units。"""
    if _utf16_len(text) <= max_utf16:
        return [text]
    chunks: list[str] = []
    current_lines: list[str] = []
    current_utf16 = 0
    for line in text.splitlines(keepends=True):
        line_utf16 = _utf16_len(line)
        if current_utf16 + line_utf16 > max_utf16 and current_lines:
            chunks.append("".join(current_lines))
            current_lines, current_utf16 = [], 0
        while line_utf16 > max_utf16:
            cut = _utf16_cut(line, max_utf16)
            chunks.append(line[:cut])
            line = line[cut:]
            line_utf16 = _utf16_len(line)
        current_lines.append(line)
        current_utf16 += line_utf16
    if current_lines:
        chunks.append("".join(current_lines))
    return chunks


def _utf16_cut(text: str, max_utf16: int) -> int:
    """返回前 max_utf16 个 UTF-16 code units 对应的字符切点。"""
    utf16_count = 0
    for i, ch in enumerate(text):
        utf16_count += 2 if ord(ch) > 0xFFFF else 1
        if utf16_count > max_utf16:
            return i
    return len(text)


def _format_tool_lines_html(tool_lines: list[dict[str, Any]]) -> str:
    if not tool_lines:
        return ""
    shown = tool_lines[-_TOOL_MAX_LINES:]
    rows = ["工具调用"]
    hidden = len(tool_lines) - len(shown)
    if hidden > 0:
        rows.append(f"... {hidden} more")
    for line in shown:
        name = line.get("name", "")
        status = line.get("status", "running")
        emoji = _tool_emoji(name)
        if status == "done":
            rows.append(f"{emoji} {name} ✅")
        else:
            rows.append(f"{emoji} {name} ...")
    if tool_lines and all(line.get("status") == "done" for line in tool_lines):
        rows.append(f"Done · {len(tool_lines)} tools")
    return "\n".join(rows)


def _format_tool_final(tool_lines: list[dict[str, Any]]) -> str:
    """最终工具调用展示格式（代码块用）。"""
    if not tool_lines:
        return ""
    shown = tool_lines[-_TOOL_MAX_LINES:]
    rows = ["工具调用"]
    hidden = len(tool_lines) - len(shown)
    if hidden > 0:
        rows.append(f"... {hidden} more")
    for line in shown:
        name = line.get("name", "")
        status = line.get("status", "running")
        emoji = _tool_emoji(name)
        intent = line.get("intent", "") or line.get("description", "")
        target = ""
        args = line.get("arguments", {})
        if isinstance(args, dict):
            for key in ("cmd", "command", "query", "url", "path", "file", "text", "name"):
                val = args.get(key)
                if val:
                    target = f" \"{str(val)[:80]}\""
                    break
        if status == "done":
            status_str = "✅"
        elif status == "error":
            status_str = "✗"
        else:
            status_str = "..."
        intent_str = f"：{intent}" if intent else ""
        rows.append(f"{emoji} {name}{intent_str}{target} {status_str}")
    if tool_lines and all(line.get("status") != "running" for line in tool_lines):
        rows.append(f"Done · {len(tool_lines)} tools")
    return "\n".join(rows)


def _format_live_html(
    thinking: str,
    tool_lines: list[dict[str, Any]],
    reply: str,
    *,
    terminal: bool,
) -> str:
    blocks: list[str] = []

    thinking_clean = thinking.strip()
    if thinking_clean:
        tail = _tail(thinking_clean, _THINKING_TAIL)
        thinking_text = f"💭 思考过程\n{tail}"
        blocks.append(f"<blockquote expandable>{_html.escape(thinking_text)}</blockquote>")

    if tool_lines:
        tool_text = _format_tool_lines_html(tool_lines)
        blocks.append(f"<pre>{_html.escape(tool_text)}</pre>")

    reply_clean = reply.strip()
    if reply_clean and not terminal:
        tail = _tail(reply_clean, _REPLY_TAIL)
        reply_text = f"回复中\n{tail}"
        blocks.append(f"<b>回复中...</b>\n{_html.escape(tail)}")

    if terminal and not blocks:
        return "推理完成"

    return "\n\n".join(blocks)


def _format_live_plain(
    thinking: str,
    tool_lines: list[dict[str, Any]],
    reply: str,
    *,
    terminal: bool,
) -> str:
    parts: list[str] = []

    thinking_clean = thinking.strip()
    if thinking_clean:
        tail = _tail(thinking_clean, _THINKING_TAIL)
        parts.append(f"💭 思考过程\n{tail}")

    if tool_lines:
        lines = ["工具调用"]
        shown = tool_lines[-_TOOL_MAX_LINES:]
        for line in shown:
            name = line.get("name", "")
            status = line.get("status", "running")
            emo = "✅" if status == "done" else "..."
            lines.append(f"{_tool_emoji(name)} {name} {emo}")
        lines.append(f"Done · {len(tool_lines)} tools")
        parts.append("\n".join(lines))

    reply_clean = reply.strip()
    if reply_clean and not terminal:
        tail = _tail(reply_clean, _REPLY_TAIL)
        parts.append(f"回复中\n{tail}")

    return "\n\n".join(parts) if parts else "推理中..."
