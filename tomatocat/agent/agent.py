"""番茄猫 Agent 核心循环（流式）"""

from __future__ import annotations

import asyncio
import base64
import logging
import mimetypes
from datetime import datetime, timedelta, timezone as _tz_utc
from pathlib import Path
from typing import Any, Awaitable, Callable

from ..config import Config
from ..bus import EventBus, InboundMessage, OutboundMessage, TurnStartEvent, TurnEndEvent
from ..session import SessionManager
from ..plugins.manager import PluginManager
from ..lifecycle import (
    BeforeTurnCtx,
    BeforeReasoningCtx,
    BeforeStepCtx,
    PromptRenderCtx,
    AfterStepCtx,
    AfterReasoningCtx,
    AfterTurnCtx,
)
from .llm import LLMProvider, LLMResponse, ToolCall

logger = logging.getLogger(__name__)

try:
    from zoneinfo import ZoneInfo
except ImportError:
    ZoneInfo = None


_VL_MAX_FILE_BYTES = 20 * 1024 * 1024
_VL_MAX_DATA_URI_BYTES = 8 * 1024 * 1024
_VL_MAX_EDGE = 4096


def _detect_image_mime_from_header(head: bytes) -> str | None:
    """从文件头检测图片格式，比依赖扩展名更可靠。"""
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if head.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if head.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if head.startswith(b"BM"):
        return "image/bmp"
    if head.startswith(b"RIFF") and head[8:12] == b"WEBP":
        return "image/webp"
    return None


def _encode_image_base64(image_path: str | Path) -> dict[str, Any] | None:
    """将本地图片转为 OpenAI 兼容的 image_url 格式（base64 data URL）。

    参考 tomatocat 的 vision.py，通过文件头检测格式，支持图片验证、缩放和压缩。
    """
    p = Path(image_path)
    if not p.is_file():
        logger.warning("[agent] 图片文件不存在: %s", p)
        return None

    file_size = p.stat().st_size
    if file_size > _VL_MAX_FILE_BYTES:
        logger.warning(
            "[agent] 图片文件过大（%dMB），上限为 %dMB",
            file_size // (1024 * 1024),
            _VL_MAX_FILE_BYTES // (1024 * 1024),
        )
        return None

    raw = p.read_bytes()
    mime = _detect_image_mime_from_header(raw[:4096])
    if not mime:
        logger.warning("[agent] 无法识别的图片格式: %s", p.name)
        return None

    try:
        from PIL import Image, ImageOps
    except ModuleNotFoundError:
        if mime not in ("image/jpeg", "image/png"):
            logger.warning(
                "[agent] 未安装 Pillow，不支持 %s 格式（仅支持 JPG/PNG），跳过: %s",
                mime,
                p.name,
            )
            return None
        try:
            b64 = base64.b64encode(raw).decode("ascii")
            if len(b64) <= _VL_MAX_DATA_URI_BYTES:
                logger.debug("[agent] 图片编码成功(无Pillow)，大小 %dKB", len(b64) // 1024)
                return {
                    "type": "image_url",
                    "image_url": {"url": f"data:{mime};base64,{b64}"},
                }
            else:
                logger.warning("[agent] 图片编码后过大，需要 Pillow 进行压缩")
                return None
        except Exception as e:
            logger.warning("[agent] 图片编码失败 %s: %s", p.name, e)
            return None

    try:
        with Image.open(p) as img:
            img.verify()
    except Exception as e:
        logger.warning("[agent] 图片文件损坏或无法解码: %s", e)
        return None

    try:
        with Image.open(p) as img:
            img = ImageOps.exif_transpose(img)
            if img.mode not in ("RGB", "L"):
                canvas = Image.new("RGB", img.size, (255, 255, 255))
                alpha = img.getchannel("A") if "A" in img.getbands() else None
                canvas.paste(img.convert("RGB"), mask=alpha)
                img = canvas
            elif img.mode == "L":
                img = img.convert("RGB")

            raw_b64_len = len(base64.b64encode(raw).decode())
            if max(img.size) > _VL_MAX_EDGE or raw_b64_len > _VL_MAX_DATA_URI_BYTES:
                img.thumbnail((_VL_MAX_EDGE, _VL_MAX_EDGE))

            if raw_b64_len <= _VL_MAX_DATA_URI_BYTES and max(img.size) <= _VL_MAX_EDGE:
                import io

                buf = io.BytesIO()
                if mime == "image/jpeg":
                    img.save(buf, format="JPEG", quality=95, optimize=True)
                    clean_mime = "image/jpeg"
                else:
                    img.save(buf, format="PNG", optimize=True)
                    clean_mime = "image/png"
                clean_b64 = base64.b64encode(buf.getvalue()).decode()
                if len(clean_b64) <= _VL_MAX_DATA_URI_BYTES:
                    logger.debug("[agent] 图片编码成功，大小 %dKB", len(clean_b64) // 1024)
                    return {
                        "type": "image_url",
                        "image_url": {"url": f"data:{clean_mime};base64,{clean_b64}", "detail": "high"},
                    }

            import io

            best: bytes | None = None
            for quality in (85, 75, 65, 55, 45):
                buf = io.BytesIO()
                img.save(buf, format="JPEG", quality=quality, optimize=True)
                candidate = buf.getvalue()
                candidate_b64 = base64.b64encode(candidate).decode()
                best = candidate
                if len(candidate_b64) <= _VL_MAX_DATA_URI_BYTES:
                    logger.debug("[agent] 图片压缩成功，质量 %d，大小 %dKB", quality, len(candidate_b64) // 1024)
                    return {
                        "type": "image_url",
                        "image_url": {"url": f"data:image/jpeg;base64,{candidate_b64}", "detail": "high"},
                    }

            if best is None:
                logger.warning("[agent] 图片压缩失败")
                return None
            best_b64 = base64.b64encode(best).decode()
            logger.warning(
                "[agent] 图片压缩后仍然过大（%dMB base64），上限为 %dMB",
                len(best_b64) // (1024 * 1024),
                _VL_MAX_DATA_URI_BYTES // (1024 * 1024),
            )
            return None

    except Exception as e:
        logger.warning("[agent] 图片处理失败 %s: %s", p.name, e)
        return None


def _weekday_cn(dt: datetime) -> str:
    days = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
    return days[dt.weekday()]


def build_current_session_prompt(channel: str, chat_id: str) -> str:
    """构建当前会话信息提示，让 AI 知道当前渠道和会话 ID"""
    return f"\n\n## 当前会话\n渠道: {channel}\n会话 ID: {chat_id}"


def build_message_time_envelope(
    message_time: datetime,
    timezone_name: str = "Asia/Shanghai",
) -> str:
    """构建消息时间信封，附加在用户消息前。"""
    if ZoneInfo:
        try:
            tzinfo = ZoneInfo(timezone_name)
        except Exception:
            tzinfo = _tz_utc(timedelta(hours=8))
    else:
        tzinfo = _tz_utc(timedelta(hours=8))

    if message_time.tzinfo is None:
        message_time = message_time.replace(tzinfo=tzinfo)
    else:
        message_time = message_time.astimezone(tzinfo)

    yesterday = message_time - timedelta(days=1)
    tomorrow = message_time + timedelta(days=1)
    day_after_tomorrow = message_time + timedelta(days=2)

    return (
        f"[当前消息时间: {message_time.strftime('%Y-%m-%d %H:%M:%S %Z')} | "
        f"request_time={message_time.isoformat()} | "
        f"今天={message_time.strftime('%Y-%m-%d')}（{_weekday_cn(message_time)}） | "
        f"昨天={yesterday.strftime('%Y-%m-%d')}（{_weekday_cn(yesterday)}） | "
        f"明天={tomorrow.strftime('%Y-%m-%d')}（{_weekday_cn(tomorrow)}） | "
        f"后天={day_after_tomorrow.strftime('%Y-%m-%d')}（{_weekday_cn(day_after_tomorrow)}） | "
        f"weekday={message_time.strftime('%A')} | "
        f"相对时间以此为准]\n"
    )


StreamDeltaFn = Callable[[str, str, str, dict[str, Any]], Awaitable[None]]


class TomatoCatAgent:
    """番茄猫核心 Agent（流式输出）"""

    def __init__(
        self,
        config: Config,
        workspace: Path,
        event_bus: EventBus,
        session_manager: SessionManager,
        plugin_manager: PluginManager,
        memory: Any = None,
        meme_service: Any = None,
        skills_loader: Any = None,
    ) -> None:
        self.config = config
        self.workspace = workspace
        self.event_bus = event_bus
        self.session_manager = session_manager
        self.plugin_manager = plugin_manager
        self.memory = memory
        self.meme_service = meme_service
        self.skills_loader = skills_loader

        self.llm = LLMProvider(
            model=config.llm_main.model,
            api_key=config.llm_main.api_key,
            base_url=config.llm_main.base_url,
            enable_thinking=config.llm_main.enable_thinking,
        )

        self._fast_llm = LLMProvider(
            model=config.llm_fast.model,
            api_key=config.llm_fast.api_key,
            base_url=config.llm_fast.base_url,
        )

        self._vl_llm: LLMProvider | None = None
        if config.llm_vl.model:
            self._vl_llm = LLMProvider(
                model=config.llm_vl.model,
                api_key=config.llm_vl.api_key,
                base_url=config.llm_vl.base_url,
                enable_thinking=config.llm_main.enable_thinking,
            )
            logger.info(f"[agent] 视觉模型已加载: {config.llm_vl.model}")

        self._system_prompt = config.agent.system_prompt

    def _build_system_with_memory(self) -> str:
        prompt = self._system_prompt
        if self.memory:
            try:
                memory_context = self.memory.get_context_block()
                if memory_context:
                    prompt += f"\n\n{memory_context}"
                    logger.debug("[agent] 记忆上下文已注入")
            except Exception as e:
                logger.warning(f"[agent] 记忆上下文获取失败: {e}")

        if self.skills_loader:
            try:
                skills_summary = self.skills_loader.build_skills_summary()
                if skills_summary:
                    prompt += f"\n\n## 可用技能\n{skills_summary}"
                    logger.info(f"[agent] 技能摘要已注入，{len(self.skills_loader.list_skills())} 个技能")

                always_skills = self.skills_loader.get_always_skills()
                if always_skills:
                    always_content = self.skills_loader.load_skills_for_context(always_skills)
                    if always_content:
                        prompt += f"\n\n## 常驻技能\n{always_content}"
                        logger.info(f"[agent] 常驻技能已注入: {always_skills}")
            except Exception as e:
                logger.warning(f"[agent] 技能加载失败: {e}")

        if self.meme_service:
            try:
                meme_block = self.meme_service.build_prompt_block()
                if meme_block:
                    prompt += f"\n\n{meme_block}"
                    logger.info(f"[agent] meme 协议已注入，长度 {len(meme_block)} 字符")
                else:
                    logger.warning("[agent] meme_block 为空，可能没有可用图片分类")
            except Exception as e:
                logger.warning(f"[agent] meme 协议注入失败: {e}")
        else:
            logger.warning("[agent] meme_service 未初始化")
        return prompt

    async def handle_message(
        self,
        session_key: str,
        text: str,
        channel: str = "cli",
        message_time: datetime | None = None,
        on_delta: StreamDeltaFn | None = None,
        media_paths: list[str] | None = None,
    ) -> dict[str, Any]:
        """处理用户消息，返回最终回复

        Args:
            session_key: 会话 key
            text: 用户消息
            channel: 渠道名
            message_time: 消息时间
            on_delta: 流式 delta 回调 (channel, chat_id, delta_type, data)
            media_paths: 附件图片本地路径列表（多模态分析）

        Returns:
            {"text": str, "media_paths": list[Path], "thinking": str, "tool_calls": list[dict]}
        """
        session = self.session_manager.get_or_create(session_key)

        if message_time is None:
            message_time = datetime.now(_tz_utc.utc)

        chat_id = session_key.split(":")[-1] if ":" in session_key else session_key

        before_turn_ctx = BeforeTurnCtx(
            session_key=session_key,
            channel=channel,
            chat_id=chat_id,
            content=text,
            timestamp=message_time,
        )
        before_turn_ctx = await self.event_bus.emit(before_turn_ctx)
        if before_turn_ctx.abort:
            logger.info("[agent] before_turn 中断，回复: %s", before_turn_ctx.abort_reply)
            return {
                "text": before_turn_ctx.abort_reply,
                "media_paths": [],
                "thinking": "",
                "tool_calls": [],
            }

        await self.event_bus.emit(TurnStartEvent(session_key))

        if not session.messages:
            system_prompt = self._build_system_with_memory()
            system_prompt += build_current_session_prompt(channel=channel, chat_id=session_key)
            session.messages.insert(0, _system_message(system_prompt))

        retrieved_memory_block = ""
        if self.memory:
            try:
                related = await self.memory.search(text, top_k=3)
                if related:
                    mem_text = "\n".join(f"- {r['content'][:80]}" for r in related)
                    retrieved_memory_block = mem_text
                    logger.info(f"[agent] 找到 {len(related)} 条相关记忆")
            except Exception:
                pass

        time_envelope = build_message_time_envelope(
            message_time,
            timezone_name=self.config.scheduler.timezone,
        )
        user_text_with_time = time_envelope + text

        image_contents: list[dict[str, Any]] = []
        if media_paths:
            for p in media_paths:
                img = _encode_image_base64(p)
                if img:
                    image_contents.append(img)
                    logger.debug(f"[agent] 图片编码成功: {p}")
                else:
                    logger.warning(f"[agent] 图片编码失败: {p}")
            if image_contents:
                logger.info("[agent] 收到 %d 张图片，启用视觉分析", len(image_contents))

        if image_contents:
            session.add_user_message(user_text_with_time, images=image_contents)
        else:
            session.add_user_message(user_text_with_time)

        final_response = ""
        final_thinking = ""
        all_tool_calls: list[dict[str, Any]] = []
        tools = self.plugin_manager.get_all_tools()
        tools_used_so_far: list[str] = []
        partial_reply = ""

        for iteration in range(self.config.agent.max_iterations):
            logger.info("[agent] 第 %d 轮推理", iteration + 1)

            messages = session.get_messages()

            before_reasoning_ctx = BeforeReasoningCtx(
                session_key=session_key,
                channel=channel,
                chat_id=chat_id,
                content=text,
                timestamp=message_time,
                skill_names=before_turn_ctx.skill_names,
                retrieved_memory_block=retrieved_memory_block,
                extra_hints=before_turn_ctx.extra_hints,
            )
            before_reasoning_ctx = await self.event_bus.emit(before_reasoning_ctx)
            if before_reasoning_ctx.abort:
                logger.info("[agent] before_reasoning 中断，回复: %s", before_reasoning_ctx.abort_reply)
                final_response = before_reasoning_ctx.abort_reply
                break

            prompt_render_ctx = PromptRenderCtx(
                session_key=session_key,
                channel=channel,
                chat_id=chat_id,
                content=text,
                timestamp=message_time,
                history=messages,
                skill_names=before_reasoning_ctx.skill_names,
                retrieved_memory_block=before_reasoning_ctx.retrieved_memory_block,
                extra_hints=before_reasoning_ctx.extra_hints,
            )
            prompt_render_ctx = await self.event_bus.emit(prompt_render_ctx)

            before_step_ctx = BeforeStepCtx(
                session_key=session_key,
                channel=channel,
                chat_id=chat_id,
                iteration=iteration,
            )
            before_step_ctx = await self.event_bus.emit(before_step_ctx)
            if before_step_ctx.early_stop:
                logger.info("[agent] before_step 提前终止，回复: %s", before_step_ctx.early_stop_reply)
                final_response = before_step_ctx.early_stop_reply
                break

            async def _delta(delta: dict[str, str]) -> None:
                if on_delta:
                    await on_delta(channel, session_key, "streaming_delta", delta)

            use_vl = False
            if self._vl_llm is not None:
                for msg in messages:
                    content = msg.get("content")
                    if isinstance(content, list):
                        for part in content:
                            if isinstance(part, dict) and part.get("type") == "image_url":
                                use_vl = True
                                break
                        if use_vl:
                            break

            active_llm = self._vl_llm if use_vl else self.llm
            if use_vl and iteration == 0:
                logger.info("[agent] 使用视觉模型进行推理")

            response = await active_llm.chat(
                messages=messages,
                tools=tools if tools else None,
                max_tokens=self.config.agent.max_tokens,
                on_delta=_delta if on_delta and iteration == 0 else None,
            )

            if response.thinking:
                final_thinking += response.thinking + "\n\n"

            if response.tool_calls:
                tool_call_dicts = [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.name,
                            "arguments": _format_arguments(tc.arguments),
                        },
                    }
                    for tc in response.tool_calls
                ]
                session.add_assistant_message(response.content or "", tool_calls=tool_call_dicts)

                tool_names = [tc.name for tc in response.tool_calls]
                tools_used_so_far.extend(tool_names)

                for tc in response.tool_calls:
                    tool_info = {"name": tc.name, "status": "running"}
                    all_tool_calls.append(tool_info)

                    if on_delta:
                        await on_delta(channel, session_key, "tool_call_start", tool_info)

                    logger.info("[agent] 调用工具: %s(%s)", tc.name, tc.arguments)
                    result = await self.plugin_manager.execute_tool(tc.name, tc.arguments)
                    logger.info("[agent] 工具结果: %s", result[:200] if result else "(空)")

                    tool_info["status"] = "done"
                    tool_info["result_preview"] = result[:200] if result else ""

                    if on_delta:
                        await on_delta(channel, session_key, "tool_call_done", tool_info)

                    session.add_tool_result(tc.id, tc.name, result)

                after_step_ctx = AfterStepCtx(
                    session_key=session_key,
                    channel=channel,
                    chat_id=chat_id,
                    iteration=iteration,
                    tools_called=tuple(tool_names),
                    partial_reply=partial_reply,
                    tools_used_so_far=tuple(tools_used_so_far),
                    has_more=True,
                )
                await self.event_bus.fanout(after_step_ctx)
                continue

            if response.content:
                final_response = response.content
                partial_reply = response.content
                session.add_assistant_message(response.content)

                after_step_ctx = AfterStepCtx(
                    session_key=session_key,
                    channel=channel,
                    chat_id=chat_id,
                    iteration=iteration,
                    tools_called=tuple(),
                    partial_reply=partial_reply,
                    tools_used_so_far=tuple(tools_used_so_far),
                    has_more=False,
                )
                await self.event_bus.fanout(after_step_ctx)
                break

        if not final_response:
            final_response = "喵... 番茄猫卡住了，请再说一次？(・_・;)"

        after_reasoning_ctx = AfterReasoningCtx(
            session_key=session_key,
            channel=channel,
            chat_id=chat_id,
            tools_used=tuple(tools_used_so_far),
            thinking=final_thinking.strip(),
            reply=final_response,
        )
        after_reasoning_ctx = await self.event_bus.emit(after_reasoning_ctx)
        final_response = after_reasoning_ctx.reply
        final_thinking = after_reasoning_ctx.thinking or final_thinking

        media_paths_list: list[Path] = []
        if self.meme_service and final_response:
            try:
                meme_result = self.meme_service.decorate_reply(final_response)
                final_response = meme_result.text
                media_paths_list = meme_result.media_paths
                if media_paths_list:
                    logger.info(f"[meme] 匹配到 {len(media_paths_list)} 个媒体")
            except Exception as e:
                logger.warning(f"[meme] 处理失败: {e}")

        if self.memory and final_response:
            try:
                self.memory.add_journal_entry(f"用户: {text[:100]}\n番茄猫: {final_response[:100]}")
            except Exception:
                pass

            asyncio.create_task(self._post_conversation_memory(text, final_response))

        after_turn_ctx = AfterTurnCtx(
            session_key=session_key,
            channel=channel,
            chat_id=chat_id,
            reply=final_response,
            tools_used=tuple(tools_used_so_far),
            thinking=final_thinking.strip(),
            will_dispatch=True,
        )
        self.event_bus.enqueue(after_turn_ctx)

        await self.event_bus.emit(TurnEndEvent(session_key, final_response))

        return {
            "text": final_response,
            "media_paths": media_paths_list,
            "thinking": final_thinking.strip(),
            "tool_calls": all_tool_calls,
        }

    async def _post_conversation_memory(self, user_text: str, assistant_text: str) -> None:
        if not self.memory:
            return

        try:
            await self.memory.extract_and_pending(
                user_text=user_text,
                assistant_text=assistant_text,
                llm_call=self._fast_llm.simple_chat,
            )

            if self.memory.tick_conversation():
                logger.info("[agent] 对话轮次达到阈值，触发记忆整合")
                await self.memory.consolidate()
        except Exception as e:
            logger.warning(f"[agent] 对话后记忆处理失败: {e}")


def _system_message(content: str) -> Any:
    from ..session import ChatMessage
    return ChatMessage(role="system", content=content)


def _format_arguments(args: dict[str, Any]) -> str:
    import json
    return json.dumps(args, ensure_ascii=False)
