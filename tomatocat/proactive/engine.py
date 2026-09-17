from __future__ import annotations

import asyncio
import json
import logging
import shutil
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from ..mcp_client import MCPClient
from ..memory import MemoryEngine

log = logging.getLogger(__name__)

_STATE_ID_LIMIT = 5_000


@dataclass
class ProactiveEvent:
    event_id: str
    source_type: str
    source_name: str
    title: str
    content: str = ""
    url: str = ""
    timestamp: float = 0.0
    ack_tool: str = ""
    server: str = ""


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
        self._delivered_ids: set[str] = set()
        self._seen_at: dict[str, float] = {}
        self._delivered_at: dict[str, float] = {}
        self._pending_acks: dict[str, dict[str, str]] = {}
        self._running = False
        self._task: asyncio.Task | None = None
        self._state_path = workspace / "proactive_state.json"
        self._load_state()

    def _load_state(self) -> None:
        state_paths = (self._state_path, self._state_path.with_suffix(self._state_path.suffix + ".bak"))
        for state_path in state_paths:
            if not state_path.exists():
                continue
            try:
                data = json.loads(state_path.read_text(encoding="utf-8"))
                self._seen_ids = set(data.get("seen_ids", []))
                self._delivered_ids = set(data.get("delivered_ids", []))
                self._seen_at = self._load_timestamps(data.get("seen_at"), self._seen_ids)
                self._delivered_at = self._load_timestamps(data.get("delivered_at"), self._delivered_ids)
                raw_pending = data.get("pending_acks", {})
                self._pending_acks = self._normalize_pending_acks(raw_pending)
                log.info(f"[proactive] 已加载状态，已处理 {len(self._seen_ids)} 条")
                return
            except Exception as e:
                log.warning("[proactive] 状态加载失败 %s: %s", state_path, e)

    @staticmethod
    def _event_key(server: str, event_id: str) -> str:
        return f"{server}:{event_id}"

    @staticmethod
    def _load_timestamps(raw: Any, ids: set[str]) -> dict[str, float]:
        now = time.time()
        timestamps = dict(raw) if isinstance(raw, dict) else {}
        return {event_id: float(timestamps.get(event_id, now)) for event_id in ids}

    def _normalize_pending_acks(self, raw: Any) -> dict[str, dict[str, str]]:
        if not isinstance(raw, dict):
            return {}
        normalized = {}
        for stored_key, metadata in raw.items():
            if not isinstance(metadata, dict):
                continue
            server = str(metadata.get("server", ""))
            event_id = str(metadata.get("event_id", stored_key))
            normalized[self._event_key(server, event_id)] = {
                "server": server,
                "ack_tool": str(metadata.get("ack_tool", "")),
                "event_id": event_id,
            }
        return normalized

    def _remember_seen(self, event: ProactiveEvent) -> None:
        key = self._event_key(event.server, event.event_id)
        self._seen_ids.add(key)
        self._seen_at[key] = time.time()

    def _remember_delivered(self, event: ProactiveEvent) -> None:
        key = self._event_key(event.server, event.event_id)
        self._delivered_ids.add(key)
        self._delivered_at[key] = time.time()

    def _is_known(self, event: ProactiveEvent) -> bool:
        key = self._event_key(event.server, event.event_id)
        # Raw IDs are retained for compatibility with pre-composite state files.
        return key in self._seen_ids or key in self._delivered_ids or event.event_id in self._seen_ids or event.event_id in self._delivered_ids

    @staticmethod
    def _prune_ids(ids: set[str], timestamps: dict[str, float]) -> None:
        now = time.time()
        for event_id in ids:
            timestamps.setdefault(event_id, now)
        keep = set(sorted(ids, key=lambda item: timestamps[item], reverse=True)[:_STATE_ID_LIMIT])
        ids.intersection_update(keep)
        for event_id in list(timestamps):
            if event_id not in keep:
                timestamps.pop(event_id, None)

    def _save_state(self) -> None:
        self._prune_ids(self._seen_ids, self._seen_at)
        self._prune_ids(self._delivered_ids, self._delivered_at)
        data = {
            "seen_ids": list(self._seen_ids),
            "delivered_ids": list(self._delivered_ids),
            "seen_at": self._seen_at,
            "delivered_at": self._delivered_at,
            "pending_acks": self._pending_acks,
        }
        tmp = self._state_path.with_suffix(self._state_path.suffix + ".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        if self._state_path.exists():
            try:
                json.loads(self._state_path.read_text(encoding="utf-8"))
                shutil.copy2(self._state_path, self._state_path.with_suffix(self._state_path.suffix + ".bak"))
            except (OSError, json.JSONDecodeError):
                pass
        tmp.replace(self._state_path)

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
                # MCP 配置缺失或对应服务启动失败时，call_tool 只会返回
                # 一段错误字符串；提前检查可避免把该字符串当 JSON 解析。
                available = {t.name for t in self.mcp.get_tools()}
                if tool_name not in available:
                    log.warning(
                        "[proactive] MCP 工具未注册: %s（当前可用: %s）",
                        tool_name,
                        ", ".join(sorted(available)) or "无",
                    )
                    continue
                result = await self.mcp.call_tool(tool_name, {})
                if isinstance(result, str):
                    if not result.strip():
                        log.warning("[proactive] %s 返回空响应", tool_name)
                        continue
                    try:
                        events_data = json.loads(result)
                    except json.JSONDecodeError as exc:
                        # Do not let a malformed/HTML MCP response abort other sources.
                        log.warning("[proactive] %s 返回非 JSON 响应: %r (%s)", tool_name, result[:200], exc)
                        continue
                else:
                    events_data = result
                if not isinstance(events_data, list):
                    continue

                for ev in events_data:
                    if ev.get("kind") != "content":
                        continue
                    event_id = ev.get("event_id", "")
                    if not event_id:
                        continue
                    event = ProactiveEvent(
                        event_id=event_id,
                        source_type=ev.get("source_type", server),
                        source_name=ev.get("source_name", server),
                        title=ev.get("title", ""),
                        content=ev.get("content", ""),
                        url=ev.get("url", ""),
                        timestamp=datetime.now().timestamp(),
                        ack_tool=source.get("ack_tool", ""),
                        server=server,
                    )
                    if not self._is_known(event):
                        events.append(event)

            except Exception as e:
                log.warning(f"[proactive] 获取 {server}/{get_tool} 失败: {e}")

        log.info(f"[proactive] 获取到 {len(events)} 条新事件")
        return events

    async def _ack_event(self, event: ProactiveEvent) -> bool:
        if not event.ack_tool or not event.server:
            return True
        try:
            result = await self.mcp.call_tool(
                f"mcp_{event.server}__{event.ack_tool}",
                {"event_ids": [event.event_id]},
            )
            if isinstance(result, str) and result.lstrip().startswith(("错误", "调用失败")):
                raise RuntimeError(result)
            event_key = self._event_key(event.server, event.event_id)
            self._pending_acks.pop(event_key, None)
            self._pending_acks.pop(event.event_id, None)
            return True
        except Exception as exc:
            event_key = self._event_key(event.server, event.event_id)
            self._pending_acks[event_key] = {
                "server": event.server,
                "ack_tool": event.ack_tool,
                "event_id": event.event_id,
            }
            log.warning("[proactive] ack failed event_id=%s: %s", event.event_id, exc)
            return False

    async def _retry_pending_acks(self) -> None:
        for stored_key, metadata in list(self._pending_acks.items()):
            event_id = metadata.get("event_id", stored_key)
            await self._ack_event(ProactiveEvent(
                event_id=event_id,
                source_type="pending_ack",
                source_name=metadata.get("server", ""),
                title="",
                server=metadata.get("server", ""),
                ack_tool=metadata.get("ack_tool", ""),
            ))
        self._save_state()

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
                if item.status == "discarded":
                    self._remember_seen(event)
                    await self._ack_event(event)

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
            await self._retry_pending_acks()
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
                # Commit delivery before acknowledging the upstream source.
                for item in interesting_items:
                    event = item.event
                    self._remember_seen(event)
                    self._remember_delivered(event)
                    await self._ack_event(event)
                self._save_state()
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
