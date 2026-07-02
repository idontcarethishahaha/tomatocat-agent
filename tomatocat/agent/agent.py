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
from .llm import LLMProvider, LLMResponse, ToolCall

logger = logging.getLogger(__name__)

try:
    from zoneinfo import ZoneInfo
except ImportError:
    ZoneInfo = None


def _encode_image_base64(image_path: str | Path) -> dict[str, Any] | None:
    """将本地图片转为 OpenAI 兼容的 image_url 格式（base64 data URL）。"""
    p = Path(image_path)
    if not p.is_file():
        return None
    mime, _ = mimetypes.guess_type(p.name)
    if not mime or not mime.startswith("image/"):
        return None
    try:
        b64 = base64.b64encode(p.read_bytes()).decode("ascii")
    except Exception as e:
        logger.warning("[agent] 图片编码失败 %s: %s", p.name, e)
        return None
    return {
        "type": "image_url",
        "image_url": {"url": f"data:{mime};base64,{b64}"},
    }


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
    ) -> None:
        self.config = config
        self.workspace = workspace
        self.event_bus = event_bus
        self.session_manager = session_manager
        self.plugin_manager = plugin_manager
        self.memory = memory
        self.meme_service = meme_service

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
        # 注入 meme 协议说明
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

        await self.event_bus.emit(TurnStartEvent(session_key))

        if not session.messages:
            system_prompt = self._build_system_with_memory()
            system_prompt += build_current_session_prompt(channel=channel, chat_id=session_key)
            session.messages.insert(0, _system_message(system_prompt))

        if self.memory:
            try:
                related = await self.memory.search(text, top_k=3)
                if related:
                    mem_text = "\n".join(f"- {r['content'][:80]}" for r in related)
                    logger.info(f"[agent] 找到 {len(related)} 条相关记忆")
            except Exception:
                pass

        if message_time is None:
            message_time = datetime.now(_tz_utc.utc)

        time_envelope = build_message_time_envelope(
            message_time,
            timezone_name=self.config.scheduler.timezone,
        )
        user_text_with_time = time_envelope + text

        # 处理图片附件（多模态）
        image_contents: list[dict[str, Any]] = []
        if media_paths:
            for p in media_paths:
                img = _encode_image_base64(p)
                if img:
                    image_contents.append(img)
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

        for iteration in range(self.config.agent.max_iterations):
            logger.info("[agent] 第 %d 轮推理", iteration + 1)

            async def _delta(delta: dict[str, str]) -> None:
                if on_delta:
                    await on_delta(channel, session_key, "streaming_delta", delta)

            messages = session.get_messages()

            # 判断是否需要用视觉模型（消息中有图片内容时）
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

                continue

            if response.content:
                final_response = response.content
                session.add_assistant_message(response.content)
                break

        if not final_response:
            final_response = "喵... 番茄猫卡住了，请再说一次？(・_・;)"

        media_paths: list[Path] = []
        if self.meme_service and final_response:
            try:
                meme_result = self.meme_service.decorate_reply(final_response)
                final_response = meme_result.text
                media_paths = meme_result.media_paths
                if media_paths:
                    logger.info(f"[meme] 匹配到 {len(media_paths)} 个媒体")
            except Exception as e:
                logger.warning(f"[meme] 处理失败: {e}")

        if self.memory and final_response:
            try:
                self.memory.add_journal_entry(f"用户: {text[:100]}\n番茄猫: {final_response[:100]}")
            except Exception:
                pass

            asyncio.create_task(self._post_conversation_memory(text, final_response))

        await self.event_bus.emit(TurnEndEvent(session_key, final_response))

        return {
            "text": final_response,
            "media_paths": media_paths,
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
                await self.memory.consolidate(self._fast_llm.simple_chat)
        except Exception as e:
            logger.warning(f"[agent] 对话后记忆处理失败: {e}")


def _system_message(content: str) -> Any:
    from ..session import ChatMessage
    return ChatMessage(role="system", content=content)


def _format_arguments(args: dict[str, Any]) -> str:
    import json
    return json.dumps(args, ensure_ascii=False)
