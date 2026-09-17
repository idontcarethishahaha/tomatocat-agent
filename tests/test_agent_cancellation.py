import asyncio

import pytest

from tomatocat.agent.agent import TomatoCatAgent
from tomatocat.bus import EventBus
from tomatocat.session import SessionManager


def test_cancelled_turn_rolls_back_session_and_tool_history():
    async def scenario():
        agent = TomatoCatAgent.__new__(TomatoCatAgent)
        agent.session_manager = SessionManager()
        agent._session_locks = {}
        agent._tool_call_history = {"desktop_chat": [{"tool": "old", "args": "{}"}]}

        session = agent.session_manager.get_or_create("desktop_chat")
        session.add_user_message("existing")

        async def cancelled_impl(**kwargs):
            session.add_user_message("partial")
            agent._tool_call_history["desktop_chat"].append(
                {"tool": "partial", "args": "{}"}
            )
            raise asyncio.CancelledError

        agent._handle_message_impl = cancelled_impl

        with pytest.raises(asyncio.CancelledError):
            await agent.handle_message("desktop_chat", "new", "desktop")

        assert [message.content for message in session.messages] == ["existing"]
        assert agent._tool_call_history["desktop_chat"] == [
            {"tool": "old", "args": "{}"}
        ]

    asyncio.run(scenario())


def test_cancelled_first_turn_removes_new_tool_history():
    async def scenario():
        agent = TomatoCatAgent.__new__(TomatoCatAgent)
        agent.session_manager = SessionManager()
        agent._session_locks = {}
        agent._tool_call_history = {}

        async def cancelled_impl(**kwargs):
            agent._tool_call_history["new_session"] = [
                {"tool": "partial", "args": "{}"}
            ]
            raise asyncio.CancelledError

        agent._handle_message_impl = cancelled_impl

        with pytest.raises(asyncio.CancelledError):
            await agent.handle_message("new_session", "new", "desktop")

        assert agent.session_manager.get("new_session").messages == []
        assert "new_session" not in agent._tool_call_history

    asyncio.run(scenario())


def test_simple_reply_is_kept_in_agent_session_history():
    async def scenario():
        agent = TomatoCatAgent.__new__(TomatoCatAgent)
        agent.session_manager = SessionManager()
        agent.event_bus = EventBus()
        agent._session_locks = {}
        agent._tool_call_history = {}

        result = await agent.handle_message("desktop_chat", "hello", "desktop")
        session = agent.session_manager.get("desktop_chat")

        assert result["text"] == "喵~ 你好呀！"
        assert [(message.role, message.content) for message in session.messages] == [
            ("user", "hello"),
            ("assistant", "喵~ 你好呀！"),
        ]
        await agent.event_bus.aclose()

    asyncio.run(scenario())
