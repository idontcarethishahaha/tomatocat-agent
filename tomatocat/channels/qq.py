"""QQ 渠道 - 通过 ncatbot + NapCat 接入

完全参考 tomatocat 项目的实现方式，使用 ncatbot SDK 管理连接。

chat_id 约定：
  私聊："qq:{user_id}"           （如 "qq:987654321"）
  群聊："qq:gqq:{group_id}"     （如 "qq:gqq:111222333"）
"""

from __future__ import annotations

import asyncio
import base64
import logging
from pathlib import Path
from typing import Any, cast

from .base import Channel

logger = logging.getLogger(__name__)

_CHANNEL = "qq"
_GROUP_PREFIX = "gqq:"

_CQ_IMAGE_RE = None


def _get_cq_image_re():
    global _CQ_IMAGE_RE
    if _CQ_IMAGE_RE is None:
        import re
        _CQ_IMAGE_RE = re.compile(r"\[CQ:image[^\]]*?(?:,|\b)url=([^,\]]+)[^\]]*\]")
    return _CQ_IMAGE_RE


def _extract_cq_images(raw: str) -> tuple[str, list[str]]:
    """从 CQ 码中提取图片 URL，返回 (纯文本, [url...])"""
    urls = _get_cq_image_re().findall(raw)
    import re
    text = re.sub(r"\[CQ:image[^\]]*\]", "", raw).strip()
    return text, urls


def _strip_at_segments(raw: str, bot_uin: str) -> str:
    import re
    text = re.sub(rf"\[CQ:at,qq={bot_uin}[^\]]*\]", "", raw).strip()
    return text


class QQChannel(Channel):
    name = _CHANNEL

    def __init__(
        self,
        bot_uin: str,
        allow_from: list[str] | None = None,
        groups: list[str] | None = None,
        upload_dir: Path | None = None,
        websocket_open_timeout_seconds: float = 5.0,
    ) -> None:
        super().__init__()
        self._bot_uin = bot_uin
        self._allow_from: set[str] = set(str(u) for u in (allow_from or []))
        self._groups: set[str] = set(str(g) for g in (groups or []))
        self._upload_dir = upload_dir or Path(".")
        self._ws_open_timeout = float(websocket_open_timeout_seconds)
        self._bot: Any = None
        self._api: Any = None
        self._main_loop: asyncio.AbstractEventLoop | None = None
        self._bot_loop: asyncio.AbstractEventLoop | None = None
        self._started = False

    async def start(self) -> None:
        if not self._bot_uin:
            logger.info("[qq] 未配置 bot_uin，跳过")
            return

        self._upload_dir.mkdir(parents=True, exist_ok=True)

        try:
            from ncatbot.core import BotClient
            from ncatbot.utils import ncatbot_config
        except ImportError:
            logger.error("[qq] ncatbot 未安装，请运行: uv pip install ncatbot")
            return

        self._patch_ws_open_timeout(self._ws_open_timeout)

        ncatbot_config.bt_uin = self._bot_uin
        allowed = list(self._allow_from)
        ncatbot_config.root = allowed[0] if allowed else self._bot_uin
        ncatbot_config.check_ncatbot_update = False
        ncatbot_config.skip_ncatbot_install_check = True
        ncatbot_config.napcat.remote_mode = True
        ncatbot_config.napcat.enable_webui = False
        ncatbot_config.enable_webui_interaction = False

        self._main_loop = asyncio.get_running_loop()
        self._bot = BotClient()

        @cast(Any, self._bot.on_private_message())
        async def _(event: Any) -> None:
            if self._bot_loop is None:
                self._bot_loop = asyncio.get_running_loop()
            user_id = str(event.user_id)

            if self._allow_from and user_id not in self._allow_from:
                logger.warning("[qq] 拒绝未授权私聊: %s", user_id)
                return

            raw: str = event.raw_message
            text, _ = _extract_cq_images(raw)

            # 从 ncatbot 消息段提取图片（比 CQ 码更可靠）
            image_segs = event.message.filter_image() if hasattr(event.message, 'filter_image') else []
            preview = text[:60] + "..." if len(text) > 60 else text
            logger.info("[qq] 私聊消息  user=%s  内容: %s  图片: %d", user_id, preview, len(image_segs))

            self._submit_to_main_loop(self._handle_private(user_id, text, image_segs))

        @cast(Any, self._bot.on_group_message())
        async def _(event: Any) -> None:
            if self._bot_loop is None:
                self._bot_loop = asyncio.get_running_loop()

            group_id = str(event.group_id)
            user_id = str(event.user_id)

            if self._groups and group_id not in self._groups:
                return

            raw = _strip_at_segments(event.raw_message, self._bot_uin)
            text, _ = _extract_cq_images(raw)

            # 从 ncatbot 消息段提取图片
            image_segs = event.message.filter_image() if hasattr(event.message, 'filter_image') else []

            if not text.strip() and not image_segs:
                return

            preview = text[:60] + "..." if len(text) > 60 else text
            logger.info("[qq] 群聊消息  group=%s  user=%s  内容: %s  图片: %d", group_id, user_id, preview, len(image_segs))

            self._submit_to_main_loop(self._handle_group(group_id, user_id, text, image_segs))

        @cast(Any, self._bot.on_startup())
        async def _(_event: Any) -> None:
            self._bot_loop = asyncio.get_running_loop()
            logger.info("[qq] NcatBot 已启动")

        logger.info("[qq] 正在启动 NcatBot...")
        self._api = await self._main_loop.run_in_executor(None, self._bot.run_backend)
        logger.info("[qq] QQ 渠道已启动")
        print("渠道已启动: qq")
        self._started = True

    async def stop(self) -> None:
        if self._api and self._bot:
            loop = asyncio.get_running_loop()
            bot_exit = getattr(self._bot, "exit", None)
            if callable(bot_exit):
                await loop.run_in_executor(None, bot_exit)
            logger.info("[qq] QQ 渠道已停止")

    def _submit_to_main_loop(self, coro: Any) -> None:
        """将 bot loop 的协程投递到主 loop 执行"""
        if self._main_loop is None:
            return
        asyncio.run_coroutine_threadsafe(coro, self._main_loop)

    def _submit_to_bot_loop(self, coro: Any) -> None:
        """将主 loop 的协程投递到 bot loop 执行"""
        if self._bot_loop is None:
            return
        asyncio.run_coroutine_threadsafe(coro, self._bot_loop)

    def _patch_ws_open_timeout(self, timeout_seconds: float) -> None:
        """覆盖 ncatbot 写死的 1 秒 WebSocket 握手超时"""
        if timeout_seconds <= 0:
            return
        try:
            import importlib
            adapter_mod = importlib.import_module("ncatbot.core.adapter.adapter")
            original_connect = getattr(adapter_mod, "_tomatocat_original_ws_connect", None)
            if original_connect is None:
                original_connect = adapter_mod.websockets.connect
                adapter_mod._tomatocat_original_ws_connect = original_connect

                def _patched_connect(*args, **kwargs):
                    configured = getattr(adapter_mod, "_tomatocat_ws_open_timeout", None)
                    if configured is not None:
                        kwargs["open_timeout"] = configured
                    return adapter_mod._tomatocat_original_ws_connect(*args, **kwargs)

                adapter_mod.websockets.connect = _patched_connect

            adapter_mod._tomatocat_ws_open_timeout = timeout_seconds
            logger.debug("[qq] ncatbot WebSocket 超时已设置为 %.1fs", timeout_seconds)
        except Exception as e:
            logger.warning("[qq] patch ncatbot WebSocket 超时失败: %s", e)

    # ── 入站处理（主 loop） ─────────────────────────────────────────

    async def _handle_private(
        self, user_id: str, text: str, image_segs: list
    ) -> None:
        """私聊入站处理"""
        session_key = f"qq:{user_id}"

        media_paths: list[str] = []
        if image_segs:
            media_paths = await self._download_image_segs(image_segs, f"qq_{user_id}")
            if media_paths:
                logger.info("[qq] 下载了 %d 张图片用于分析", len(media_paths))

        result = await self._handle_message(
            session_key, text, "qq", media_paths=media_paths
        )
        reply_text = result.get("text", "")
        result_media = result.get("media_paths", [])
        thinking = result.get("thinking", "")
        tool_calls = result.get("tool_calls", [])

        process_msg = _format_qq_process(thinking, tool_calls)
        if process_msg:
            await self._send_private_text(user_id, process_msg)

        if reply_text:
            await self._send_private_text(user_id, reply_text)
        for media_path in result_media:
            try:
                await self.send_image(user_id, str(media_path))
            except Exception as e:
                logger.error("[qq] 图片发送失败: %s", e)

    async def _handle_group(
        self, group_id: str, user_id: str, text: str, image_segs: list
    ) -> None:
        """群聊入站处理"""
        chat_id = f"{_GROUP_PREFIX}{group_id}"
        session_key = f"qq:{chat_id}"

        media_paths: list[str] = []
        if image_segs:
            media_paths = await self._download_image_segs(image_segs, f"qq_g{group_id}")
            if media_paths:
                logger.info("[qq] 下载了 %d 张图片用于分析", len(media_paths))

        result = await self._handle_message(
            session_key, text, "qq", media_paths=media_paths
        )
        reply_text = result.get("text", "")
        result_media = result.get("media_paths", [])
        thinking = result.get("thinking", "")
        tool_calls = result.get("tool_calls", [])

        process_msg = _format_qq_process(thinking, tool_calls)
        if process_msg:
            await self._send_group_text(group_id, process_msg)

        if reply_text:
            reply_with_at = f"[CQ:at,qq={user_id}]\n{reply_text}"
            await self._send_group_text(group_id, reply_with_at)
        for media_path in result_media:
            try:
                await self.send_image(f"{_GROUP_PREFIX}{group_id}", str(media_path))
            except Exception as e:
                logger.error("[qq] 图片发送失败: %s", e)

    # ── 出站（需要投递到 bot loop） ─────────────────────────────────

    async def send_message(self, chat_id: str, text: str) -> None:
        """发送主动消息"""
        if not text or not self._api:
            return
        raw = str(chat_id)
        if raw.startswith("qq:"):
            raw = raw[3:]
        if raw.startswith(_GROUP_PREFIX):
            group_id = raw[len(_GROUP_PREFIX):]
            await self._send_group_text(group_id, text)
        else:
            await self._send_private_text(raw, text)

    async def send_image(self, chat_id: str, image_path: str) -> None:
        """发送图片"""
        raw = str(chat_id)
        if raw.startswith("qq:"):
            raw = raw[3:]
        if raw.startswith(_GROUP_PREFIX):
            group_id = raw[len(_GROUP_PREFIX):]
            await self._send_group_image(group_id, image_path)
        else:
            await self._send_private_image(raw, image_path)

    def _path_to_cq_image(self, path: str) -> str:
        if path.startswith(("http://", "https://")):
            return f"[CQ:image,file={path}]"
        try:
            data = Path(path).read_bytes()
            b64 = base64.b64encode(data).decode()
            return f"[CQ:image,file=base64://{b64}]"
        except Exception as e:
            logger.warning("[qq] 图片读取失败: %s", e)
            return f"[CQ:image,file={path}]"

    async def _send_private_text(self, user_id: str, text: str) -> None:
        if not self._api or self._bot_loop is None:
            return
        try:
            msg_segments = [{"type": "text", "data": {"text": text}}]
            fut = asyncio.run_coroutine_threadsafe(
                self._api.send_private_msg(user_id=user_id, message=msg_segments),
                self._bot_loop,
            )
            await asyncio.wrap_future(fut)
        except Exception as e:
            logger.error("[qq] 私聊消息发送失败: %s", e)

    async def _send_group_text(self, group_id: str, text: str) -> None:
        if not self._api or self._bot_loop is None:
            return
        try:
            msg_segments = [{"type": "text", "data": {"text": text}}]
            fut = asyncio.run_coroutine_threadsafe(
                self._api.send_group_msg(group_id=group_id, message=msg_segments),
                self._bot_loop,
            )
            await asyncio.wrap_future(fut)
        except Exception as e:
            logger.error("[qq] 群聊消息发送失败: %s", e)

    async def _send_private_image(self, user_id: str, image_path: str) -> None:
        if not self._api or self._bot_loop is None:
            return
        try:
            fut = asyncio.run_coroutine_threadsafe(
                self._api.send_private_image(user_id=user_id, image=image_path),
                self._bot_loop,
            )
            await asyncio.wrap_future(fut)
        except Exception as e:
            logger.error("[qq] 私聊图片发送失败: %s", e)

    async def _send_group_image(self, group_id: str, image_path: str) -> None:
        if not self._api or self._bot_loop is None:
            return
        try:
            fut = asyncio.run_coroutine_threadsafe(
                self._api.send_group_image(group_id=group_id, image=image_path),
                self._bot_loop,
            )
            await asyncio.wrap_future(fut)
        except Exception as e:
            logger.error("[qq] 群聊图片发送失败: %s", e)

    # ── 图片下载 ───────────────────────────────────────────────────

    async def _download_image_segs(self, image_segs: list, prefix: str) -> list[str]:
        """从 ncatbot Image 消息段下载图片到本地"""
        import time

        self._upload_dir.mkdir(parents=True, exist_ok=True)
        paths: list[str] = []

        for i, seg in enumerate(image_segs):
            try:
                ext = ".jpg"
                if hasattr(seg, 'is_animated_image') and seg.is_animated_image():
                    ext = ".gif"
                filename = f"{prefix}_{int(time.time() * 1000)}_{i}{ext}"
                save_path = self._upload_dir / filename

                # download_to 是异步方法，需要在 bot loop 中调用
                if hasattr(seg, 'download_to'):
                    if self._bot_loop:
                        import inspect
                        if inspect.iscoroutinefunction(seg.download_to):
                            # 异步方法，投递到 bot loop
                            fut = asyncio.run_coroutine_threadsafe(
                                seg.download_to(str(save_path)),
                                self._bot_loop,
                            )
                            await asyncio.wrap_future(fut)
                        else:
                            # 同步方法
                            await asyncio.to_thread(seg.download_to, str(save_path))
                    else:
                        await asyncio.to_thread(seg.download_to, str(save_path))
                    paths.append(str(save_path))
                    logger.debug("[qq] 图片下载成功(native): %s", save_path)
                    continue

                # 回退：用 url 属性 + httpx 下载
                url = getattr(seg, 'url', None)
                if url:
                    downloaded = await self._download_images([url], prefix)
                    paths.extend(downloaded)
                else:
                    logger.warning("[qq] 图片段无 url 也无 download_to: %s", type(seg))

            except Exception as e:
                url = getattr(seg, 'url', None)
                if url:
                    try:
                        downloaded = await self._download_images([url], prefix)
                        paths.extend(downloaded)
                    except Exception as e2:
                        logger.warning("[qq] 图片下载失败(url回退): %s", e2)
                else:
                    logger.warning("[qq] 图片下载失败: %s", e)

        return paths

    async def _download_images(self, urls: list[str], prefix: str) -> list[str]:
        """下载图片 URL 到本地，返回路径列表（httpx 回退方案）"""
        import httpx
        import time

        self._upload_dir.mkdir(parents=True, exist_ok=True)
        paths: list[str] = []
        ext_map = {
            "image/jpeg": ".jpg",
            "image/png": ".png",
            "image/gif": ".gif",
            "image/webp": ".webp",
        }
        async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
            for i, url in enumerate(urls):
                try:
                    resp = await client.get(url)
                    if resp.status_code != 200:
                        logger.warning("[qq] 图片下载失败 status=%d url=%s", resp.status_code, url[:80])
                        continue
                    ct = resp.headers.get("content-type", "image/jpeg").split(";")[0].strip()
                    ext = ext_map.get(ct, ".jpg")
                    filename = f"{prefix}_{int(time.time() * 1000)}_{i}{ext}"
                    save_path = self._upload_dir / filename
                    save_path.write_bytes(resp.content)
                    paths.append(str(save_path))
                    logger.debug("[qq] 图片下载成功: %s -> %s", url[:60], save_path)
                except Exception as e:
                    logger.warning("[qq] 图片下载异常 url=%s err=%s", url[:80], e)
        return paths


def _format_qq_process(thinking: str, tool_calls: list[dict[str, Any]]) -> str:
    """QQ 过程记录格式化（纯文本）"""
    parts: list[str] = []

    thinking_stripped = thinking.strip()
    if thinking_stripped:
        display = thinking_stripped[:500]
        if len(thinking_stripped) > 500:
            display += "..."
        parts.append(f"💭 思考过程\n{display}")

    if tool_calls:
        lines = ["🔧 工具调用"]
        for tc in tool_calls:
            name = tc.get("name", "")
            args = tc.get("arguments", {})
            if isinstance(args, dict):
                intent = ""
                for key in ("description", "query", "summary", "task", "action"):
                    val = args.get(key)
                    if isinstance(val, str) and val.strip():
                        intent = val.strip()[:80]
                        break
                target = ""
                for key in ("cmd", "command", "query", "url", "path", "file", "text", "name"):
                    val = args.get(key)
                    if val:
                        target = f" \"{str(val)[:60]}\""
                        break
                emoji = _tool_emoji(name)
                lines.append(f"{emoji} {name[:32]}: {intent}{target} ✅")
            else:
                lines.append(f"{_tool_emoji(name)} {name[:32]} ✅")
        lines.append(f"Done · {len(tool_calls)} tools")
        if parts:
            parts.append("")
        parts.append("\n".join(lines))

    if not parts:
        return ""

    return "\n".join(parts)


def _tool_emoji(tool_name: str) -> str:
    name = tool_name.lower()
    if name.startswith("mcp"):
        return "📡"
    if "search" in name or "fetch" in name:
        return "🔍"
    if "schedule" in name or "cancel" in name:
        return "⏰"
    if "shell" in name:
        return "⚙️"
    if "file" in name or "read" in name or "write" in name:
        return "📄"
    return "🔧"
