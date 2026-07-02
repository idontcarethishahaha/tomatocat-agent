"""番茄猫 Agent 核心循环"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone as _tz_utc
from pathlib import Path
from typing import Any

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
    """构建消息时间信封，附加在用户消息前。

    包含：
    - 当前消息时间（本地时区可读格式）
    - request_time（ISO 格式，用于 schedule 工具延迟补偿）
    - 今天/昨天/明天/后天 日期
    - 星期
    """
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


class TomatoCatAgent:
    """番茄猫核心 Agent"""

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

        # 快速 LLM（用于记忆提取和整合，用小模型省 token）
        self._fast_llm = LLMProvider(
            model=config.llm_fast.model,
            api_key=config.llm_fast.api_key,
            base_url=config.llm_fast.base_url,
        )

        self._system_prompt = config.agent.system_prompt

    def _build_system_with_memory(self) -> str:
        prompt = self._system_prompt
        if self.memory:
            try:
                memory_context = self.memory.get_context_block()
                if memory_context:
                    prompt += f"\n\n{memory_context}"
            except Exception as e:
                logger.warning(f"[agent] 记忆上下文获取失败: {e}")
        # 注入 meme 协议说明（只有有素材时才注入）
        if self.meme_service:
            try:
                meme_block = self.meme_service.build_prompt_block()
                if meme_block:
                    prompt += f"\n\n{meme_block}"
            except Exception as e:
                logger.warning(f"[agent] meme 协议注入失败: {e}")
        return prompt

    async def handle_message(
        self,
        session_key: str,
        text: str,
        channel: str = "cli",
        message_time: datetime | None = None,
    ) -> dict[str, Any]:
        """处理用户消息，返回 {"text": str, "media_paths": list[Path]}"""
        session = self.session_manager.get_or_create(session_key)

        await self.event_bus.emit(TurnStartEvent(session_key))

        # 命令拦截：/ping /status /version /help 等不经过 LLM
        if text.strip().startswith("/"):
            status_plugin = self.plugin_manager.plugins.get("status_commands")
            if status_plugin:
                cmd_reply = status_plugin.handle_command(text, session_key, channel)
                if cmd_reply is not None:
                    await self.event_bus.emit(TurnEndEvent(session_key, cmd_reply))
                    return {"text": cmd_reply, "media_paths": []}

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
        session.add_user_message(user_text_with_time)

        final_response = ""
        tools = self.plugin_manager.get_all_tools()

        for iteration in range(self.config.agent.max_iterations):
            logger.info("[agent] 第 %d 轮推理", iteration + 1)

            messages = session.get_messages()
            response = await self.llm.chat(
                messages=messages,
                tools=tools if tools else None,
                max_tokens=self.config.agent.max_tokens,
            )

            if response.thinking:
                logger.info("[agent] 思考过程: %s", response.thinking[:200])

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
                    logger.info("[agent] 调用工具: %s(%s)", tc.name, tc.arguments)
                    result = await self.plugin_manager.execute_tool(tc.name, tc.arguments)
                    logger.info("[agent] 工具结果: %s", result[:200] if result else "(空)")
                    session.add_tool_result(tc.id, tc.name, result)

                continue

            if response.content:
                final_response = response.content
                session.add_assistant_message(response.content)
                break

        if not final_response:
            final_response = "喵... 番茄猫卡住了，请再说一次？(・_・;)"

        # meme 表情包处理：提取 <meme:tag> 标签，返回媒体路径
        meme_media_paths: list[Any] = []
        if self.meme_service and final_response:
            try:
                meme_result = self.meme_service.decorate_reply(final_response)
                final_response = meme_result.text
                meme_media_paths = meme_result.media_paths
            except Exception as e:
                logger.warning(f"[meme] 处理失败: {e}")

        if self.memory and final_response:
            try:
                self.memory.add_journal_entry(f"用户: {text[:100]}\n番茄猫: {final_response[:100]}")
            except Exception:
                pass

            # 异步提取记忆（不阻塞回复）
            asyncio.create_task(self._post_conversation_memory(text, final_response))

        await self.event_bus.emit(TurnEndEvent(session_key, final_response))

        return {"text": final_response, "media_paths": meme_media_paths}

    async def _post_conversation_memory(self, user_text: str, assistant_text: str) -> None:
        """对话后异步处理：提取记忆 → 定时整合"""
        if not self.memory:
            return

        try:
            # 1. 用快速 LLM 提取关键信息到 PENDING
            await self.memory.extract_and_pending(
                user_text=user_text,
                assistant_text=assistant_text,
                llm_call=self._fast_llm.simple_chat,
            )

            # 2. 检查是否该触发整合
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
