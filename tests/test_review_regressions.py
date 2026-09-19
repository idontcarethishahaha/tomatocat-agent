import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from tomatocat.agent.agent import TomatoCatAgent
from tomatocat.agent.llm import LLMResponse, ToolCall
from tomatocat.agent.skills import SkillsLoader
from tomatocat.bus import EventBus
from tomatocat.core.memory.engine import MemoryQueryResult, MemoryRecord
from tomatocat.dashboard_api import _resolve_workspace_path
from tomatocat.lifecycle import PromptRenderCtx
from tomatocat.plugins.default_memory.engine import DefaultMemoryEngine
from tomatocat.core.memory.engine import MemoryIngestRequest
from tomatocat.session import ChatMessage, Session, SessionManager
from tomatocat.channels.telegram import TelegramChannel
from plugins.memory2.plugin import Memory2Plugin


def test_unfiltered_skill_listing_returns_all_skills(tmp_path):
    skill_dir = tmp_path / "skills" / "demo"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("# demo", encoding="utf-8")
    loader = SkillsLoader(tmp_path, builtin_skills_dir=tmp_path / "builtins")
    assert [skill["name"] for skill in loader.list_skills(False)] == ["demo"]


def test_dashboard_path_rejects_prefix_sibling(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    with pytest.raises(HTTPException) as exc_info:
        _resolve_workspace_path(workspace, "../workspace-secret/file.txt")
    assert exc_info.value.status_code == 403


def _agent_shell(llm, memory=None):
    agent = TomatoCatAgent.__new__(TomatoCatAgent)
    agent.config = SimpleNamespace(
        scheduler=SimpleNamespace(timezone="Asia/Shanghai"),
        agent=SimpleNamespace(max_iterations=3, max_tokens=100),
    )
    agent.session_manager = SessionManager()
    agent.event_bus = EventBus()
    agent.plugin_manager = SimpleNamespace(get_all_tools=lambda: [], execute_tool=None)
    agent.memory = memory
    agent.meme_service = None
    agent.skills_loader = None
    agent.llm = llm
    agent._vl_llm = None
    agent._fast_llm = SimpleNamespace(simple_chat=None)
    agent._system_prompt = "base"
    agent._context_token_limit = 12_000
    agent._context_warning_threshold = 8_000
    agent._session_locks = {}
    agent._tool_call_history = {}
    agent._tool_loop_threshold = 3
    return agent


def test_retrieved_memory_and_prompt_hook_reach_llm():
    class Memory:
        def get_context_block(self):
            return ""

        async def query(self, _request):
            return MemoryQueryResult(
                text_block="remembered fact",
                records=[MemoryRecord("1", "fact", "remembered fact", 1.0, "test")],
            )

        def add_journal_entry(self, _content):
            pass

        async def extract_and_pending(self, **_kwargs):
            pass

        def tick_conversation(self):
            return False

    class LLM:
        def __init__(self):
            self.messages = []

        async def chat(self, *, messages, **_kwargs):
            self.messages = messages
            return LLMResponse(content="done")

    async def scenario():
        llm = LLM()
        agent = _agent_shell(llm, Memory())

        def add_hook_context(ctx):
            ctx.history.append({"role": "system", "content": "hook context"})
            return ctx

        agent.event_bus.on(PromptRenderCtx, add_hook_context)
        await agent.handle_message("desktop:test", "what did I say before?", "desktop")
        contents = [str(message.get("content", "")) for message in llm.messages]
        assert any("remembered fact" in content for content in contents)
        assert "hook context" in contents
        await asyncio.sleep(0)
        await agent.event_bus.aclose()

    asyncio.run(scenario())


def test_tool_guard_completes_all_tool_call_results():
    class LLM:
        def __init__(self):
            self.calls = 0

        async def chat(self, **_kwargs):
            self.calls += 1
            return LLMResponse(tool_calls=[
                ToolCall("one", "shell", {"command": "x"}),
                ToolCall("two", "read_file", {"path": "x"}),
            ])

    async def scenario():
        llm = LLM()
        agent = _agent_shell(llm)
        agent._check_tool_loop = lambda *_args: (True, "loop")
        await agent.handle_message("desktop:test", "run this", "desktop")
        session = agent.session_manager.get("desktop:test")
        tool_results = [message for message in session.messages if message.role == "tool"]
        assert llm.calls == 1
        assert [message.tool_call_id for message in tool_results] == ["one", "two"]
        await agent.event_bus.aclose()

    asyncio.run(scenario())


def test_session_trim_drops_orphaned_tool_results():
    session = Session("test", max_history=3)
    session.messages = [
        ChatMessage("assistant", "", tool_calls=[{"id": "old"}]),
        ChatMessage("tool", "old result", tool_call_id="old"),
        ChatMessage("user", "new"),
    ]
    session.add_assistant_message("answer")
    assert session.messages[0].role == "user"


def test_telegram_delivery_exception_is_propagated():
    class Bot:
        async def send_message(self, **_kwargs):
            raise ValueError("rejected")

    channel = TelegramChannel("token")
    channel._application = SimpleNamespace(bot=Bot())
    with pytest.raises(ValueError, match="rejected"):
        asyncio.run(channel.send_message("123", "hello"))


def test_telegram_stale_pending_updates_restart_polling(monkeypatch):
    async def scenario():
        channel = TelegramChannel("token")
        channel._application = SimpleNamespace(updater=SimpleNamespace())
        channel._polling = True
        restarted = []

        async def probe():
            return 2

        async def restart(reason):
            restarted.append(reason)
            channel._polling = False

        async def no_wait(_seconds):
            return None

        channel._probe_pending_updates = probe
        channel._restart_polling = restart
        monkeypatch.setattr(asyncio, "sleep", no_wait)
        await channel._watch_polling()
        assert restarted == ["2 条更新持续积压"]

    asyncio.run(scenario())


def test_memory_ingest_returns_string_id():
    class Embedder:
        async def embed(self, _content):
            return [1.0]

    class Store:
        async def add(self, **_kwargs):
            return SimpleNamespace(id="memory-id")

    engine = DefaultMemoryEngine.__new__(DefaultMemoryEngine)
    engine._embedder = Embedder()
    engine._vec_store = Store()
    engine._event_bus = None
    result = asyncio.run(engine.ingest(MemoryIngestRequest(content="remember", source_kind="test")))
    assert result.created_ids == ["memory-id"]


def test_memory2_reads_active_config(tmp_path):
    config_path = tmp_path / "custom.toml"
    config_path.write_text(
        '[memory2]\nenabled = true\nmodel = "active-model"\n',
        encoding="utf-8",
    )
    plugin = Memory2Plugin()
    plugin.context = SimpleNamespace(shared={"config_path": config_path})
    assert plugin._get_config() == {"enabled": True, "model": "active-model"}


def test_memory2_delete_reports_missing_item():
    plugin = Memory2Plugin()
    plugin._enabled = True
    plugin._store = SimpleNamespace(delete=lambda _item_id: False)
    result = asyncio.run(plugin.memory_delete(None, "missing"))
    assert "未找到" in result


def test_shutdown_continues_after_cleanup_error():
    from main import _shutdown_context

    calls = []

    class Dashboard:
        def stop(self):
            calls.append("dashboard")
            raise RuntimeError("stop failed")

    class AsyncResource:
        async def close(self):
            calls.append("mcp")

    class Lock:
        def release(self):
            calls.append("lock")

    ctx = {
        "dashboard": Dashboard(),
        "mcp_client": AsyncResource(),
        "instance_lock": Lock(),
        "channels": [],
    }
    asyncio.run(_shutdown_context(ctx))
    assert calls == ["dashboard", "mcp", "lock"]
