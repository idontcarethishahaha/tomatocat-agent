from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from ..mcp_client import MCPClient
from ..memory import MemoryEngine

log = logging.getLogger(__name__)


@dataclass
class ProactiveEvent:
    event_id: str
    source_type: str
    source_name: str
    title: str
    content: str = ""
    url: str = ""
    timestamp: float = 0.0


@dataclass
class ProactiveItem:
    event: ProactiveEvent
    score: float = 0.0
    reason: str = ""
    status: str = "new"  # new / interesting / discarded / delivered


class ProactiveEngine:
    def __init__(
        self,
        workspace: Path,
        mcp: MCPClient,
        memory: MemoryEngine,
        llm_call_fn: Any,
        send_fn: Any,
        poll_interval: int = 300,
        target_channel: str = "telegram",
        target_chat_id: str = "",
        sources_config_file: str = "proactive_sources.json",
    ):
        self.workspace = workspace
        self.mcp = mcp
        self.memory = memory
        self._llm_call = llm_call_fn
        self._send = send_fn
        self.poll_interval = poll_interval
        self.target_channel = target_channel
        self.target_chat_id = target_chat_id
        self.sources_config_file = sources_config_file
        self._items: dict[str, ProactiveItem] = {}
        self._seen_ids: set[str] = set()
        self._running = False
        self._task: asyncio.Task | None = None
        self._state_path = workspace / "proactive_state.json"
        self._load_state()

    def _load_state(self) -> None:
        if self._state_path.exists():
            try:
                data = json.loads(self._state_path.read_text(encoding="utf-8"))
                self._seen_ids = set(data.get("seen_ids", []))
                log.info(f"[proactive] 已加载状态，已处理 {len(self._seen_ids)} 条")
            except Exception as e:
                log.warning(f"[proactive] 状态加载失败: {e}")

    def _save_state(self) -> None:
        data = {"seen_ids": list(self._seen_ids)}
        self._state_path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def _load_sources(self) -> list[dict[str, Any]]:
        p = self.workspace / self.sources_config_file
        if not p.exists():
            return []
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            return [s for s in data.get("sources", []) if s.get("enabled", False) and s.get("channel") == "content"]
        except Exception as e:
            log.warning(f"[proactive] 源配置加载失败: {e}")
            return []

    async def _fetch_events(self) -> list[ProactiveEvent]:
        sources = self._load_sources()
        events = []

        for source in sources:
            server = source.get("server", "")
            get_tool = source.get("get_tool", "")
            if not server or not get_tool:
                continue

            tool_name = f"mcp_{server}__{get_tool}"
            try:
                result = await self.mcp.call_tool(tool_name, {})
                events_data = json.loads(result) if isinstance(result, str) else result
                if not isinstance(events_data, list):
                    continue

                for ev in events_data:
                    if ev.get("kind") != "content":
                        continue
                    event_id = ev.get("event_id", "")
                    if not event_id or event_id in self._seen_ids:
                        continue

                    events.append(ProactiveEvent(
                        event_id=event_id,
                        source_type=ev.get("source_type", server),
                        source_name=ev.get("source_name", server),
                        title=ev.get("title", ""),
                        content=ev.get("content", ""),
                        url=ev.get("url", ""),
                        timestamp=datetime.now().timestamp(),
                    ))

                    ack_tool = source.get("ack_tool")
                    if ack_tool:
                        ack_name = f"mcp_{server}__{ack_tool}"
                        try:
                            await self.mcp.call_tool(ack_name, {"event_ids": [event_id]})
                        except Exception:
                            pass

            except Exception as e:
                log.warning(f"[proactive] 获取 {server}/{get_tool} 失败: {e}")

        log.info(f"[proactive] 获取到 {len(events)} 条新事件")
        return events

    async def _judge_interesting(self, events: list[ProactiveEvent]) -> list[ProactiveItem]:
        if not events:
            return []

        memory_context = self.memory.get_context_block()[:500]
        items = []

        for event in events:
            prompt = f"""请判断以下内容是否值得推送给用户。

【用户记忆摘要】
{memory_context}

【待判断内容】
标题: {event.title}
来源: {event.source_name}
内容: {event.content[:300]}

请回答：
- score: 0-10 的数字，越高越值得推送
- reason: 一句话说明为什么
- interesting: yes 或 no

只输出 JSON，不要其他文字。"""

            try:
                result = await self._llm_call(prompt)
                text = result if isinstance(result, str) else result.get("content", "")

                score = 5.0
                reason = ""
                interesting = False

                import re
                json_match = re.search(r"\{.*\}", text, re.DOTALL)
                if json_match:
                    try:
                        data = json.loads(json_match.group())
                        score = float(data.get("score", 5.0))
                        reason = str(data.get("reason", ""))
                        interesting = str(data.get("interesting", "no")).lower() == "yes"
                    except Exception:
                        pass

                item = ProactiveItem(
                    event=event,
                    score=score,
                    reason=reason,
                    status="interesting" if interesting and score >= 6 else "discarded",
                )
                items.append(item)
                self._seen_ids.add(event.event_id)

            except Exception as e:
                log.warning(f"[proactive] 判断失败: {e}")

        self._save_state()
        return items

    async def _generate_push_message(self, items: list[ProactiveItem]) -> str:
        if not items:
            return ""

        items_text = "\n\n".join(
            f"{i+1}. {item.event.title}\n   {item.event.content[:150]}\n   {item.event.url}"
            for i, item in enumerate(items[:5])
        )

        prompt = f"""请把以下有趣的内容整理成一条亲切的推送消息，用番茄猫的口吻。

要求：
- 用可爱的语气，带颜文字
- 每条内容用简短的话概括
- 最后引导用户回复讨论

【内容】
{items_text}"""

        try:
            result = await self._llm_call(prompt)
            return result if isinstance(result, str) else result.get("content", "")
        except Exception as e:
            log.warning(f"[proactive] 生成推送消息失败: {e}")
            return f"喵~ 为你找到 {len(items)} 条有意思的内容！(≧∇≦)ﾉ\n\n{items_text}"

    async def _tick(self) -> None:
        try:
            events = await self._fetch_events()
            if not events:
                return

            items = await self._judge_interesting(events)
            interesting_items = [i for i in items if i.status == "interesting"]

            if not interesting_items:
                log.info("[proactive] 没有值得推送的内容")
                return

            log.info(f"[proactive] 有 {len(interesting_items)} 条值得推送")

            message = await self._generate_push_message(interesting_items)
            if message:
                await self._send(
                    self.target_channel,
                    self.target_chat_id,
                    message,
                )
                log.info("[proactive] 推送已发送")

        except Exception as e:
            log.error(f"[proactive] tick 出错: {e}")

    async def _loop(self) -> None:
        log.info(f"[proactive] 主动推送循环已启动，间隔 {self.poll_interval}s")
        while self._running:
            await self._tick()
            await asyncio.sleep(self.poll_interval)

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self._save_state()
        log.info("[proactive] 已停止")
