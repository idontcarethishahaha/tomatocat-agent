import asyncio
import json

from tomatocat.proactive.engine import ProactiveEngine


class FakeMCP:
    def __init__(self):
        self.acks = []
    def get_tools(self):
        return [
            type("Tool", (), {"name": "mcp_feed__get"})(),
            type("Tool", (), {"name": "mcp_feed__ack"})(),
        ]
    async def call_tool(self, name, args):
        if name.endswith("__get"):
            return json.dumps([])
        self.acks.append((name, args))


def test_send_failure_does_not_ack_or_mark_delivered(tmp_path):
    mcp = FakeMCP()
    sent = 0

    async def call_tool(name, args):
        if name.endswith("__get"):
            return json.dumps([{
                "kind": "content", "event_id": "new-event", "title": "News",
                "source_name": "feed", "content": "Something happened",
            }])
        mcp.acks.append((name, args))

    async def judge(_prompt):
        return '{"score": 8, "reason": "relevant", "interesting": "yes"}'

    async def send(*_args):
        nonlocal sent
        sent += 1
        raise RuntimeError("offline")

    mcp.call_tool = call_tool
    engine = ProactiveEngine(tmp_path, mcp, memory=type("M", (), {"get_context_block": lambda self: ""})(), llm_call_fn=judge, send_fn=send)
    engine._load_sources = lambda: [{"enabled": True, "channel": "content", "server": "feed", "get_tool": "get", "ack_tool": "ack"}]
    asyncio.run(engine._tick())
    assert sent == 1
    assert mcp.acks == []
    assert "new-event" not in engine._seen_ids
    assert "new-event" not in engine._delivered_ids


def test_failed_ack_is_persisted_and_retried(tmp_path):
    class FlakyMCP(FakeMCP):
        def __init__(self):
            super().__init__()
            self.fail = True

        async def call_tool(self, name, args):
            if self.fail:
                self.fail = False
                raise RuntimeError("offline")
            self.acks.append((name, args))

    mcp = FlakyMCP()
    engine = ProactiveEngine(
        tmp_path, mcp,
        memory=type("M", (), {"get_context_block": lambda self: ""})(),
        llm_call_fn=lambda *_: None,
        send_fn=lambda *_: None,
    )
    event = __import__("tomatocat.proactive.engine", fromlist=["ProactiveEvent"]).ProactiveEvent(
        event_id="event-1", source_type="feed", source_name="feed", title="x",
        server="feed", ack_tool="ack",
    )
    assert asyncio.run(engine._ack_event(event)) is False
    assert "feed:event-1" in engine._pending_acks
    asyncio.run(engine._retry_pending_acks())
    assert "event-1" not in engine._pending_acks
    restored = ProactiveEngine(
        tmp_path, mcp,
        memory=type("M", (), {"get_context_block": lambda self: ""})(),
        llm_call_fn=lambda *_: None,
        send_fn=lambda *_: None,
    )
    assert restored._pending_acks == {}


def test_mcp_error_string_keeps_ack_pending(tmp_path):
    class ErrorMCP(FakeMCP):
        async def call_tool(self, _name, _args):
            return "调用失败: offline"

    engine = ProactiveEngine(
        tmp_path, ErrorMCP(),
        memory=type("M", (), {"get_context_block": lambda self: ""})(),
        llm_call_fn=lambda *_: None,
        send_fn=lambda *_: None,
    )
    event = __import__("tomatocat.proactive.engine", fromlist=["ProactiveEvent"]).ProactiveEvent(
        event_id="event-error", source_type="feed", source_name="feed", title="x",
        server="feed", ack_tool="ack",
    )
    assert asyncio.run(engine._ack_event(event)) is False
    assert "feed:event-error" in engine._pending_acks


def test_state_save_replaces_temp_file(tmp_path):
    engine = ProactiveEngine(
        tmp_path, FakeMCP(),
        memory=type("M", (), {"get_context_block": lambda self: ""})(),
        llm_call_fn=lambda *_: None,
        send_fn=lambda *_: None,
    )
    engine._seen_ids.add("saved")
    engine._save_state()
    state = json.loads((tmp_path / "proactive_state.json").read_text(encoding="utf-8"))
    assert state["seen_ids"] == ["saved"]
    assert not (tmp_path / "proactive_state.json.tmp").exists()


def test_same_event_id_from_another_source_is_not_discarded(tmp_path):
    mcp = FakeMCP()

    async def call_tool(name, _args):
        assert name == "mcp_feed__get"
        return json.dumps([{"kind": "content", "event_id": "same", "title": "second"}])

    mcp.call_tool = call_tool
    engine = ProactiveEngine(
        tmp_path, mcp,
        memory=type("M", (), {"get_context_block": lambda self: ""})(),
        llm_call_fn=lambda *_: None,
        send_fn=lambda *_: None,
    )
    engine._seen_ids.add("other:same")
    engine._load_sources = lambda: [{
        "enabled": True, "channel": "content", "server": "feed",
        "get_tool": "get", "ack_tool": "ack",
    }]
    events = asyncio.run(engine._fetch_events())
    assert [event.event_id for event in events] == ["same"]


def test_state_ids_are_pruned_by_recency(monkeypatch, tmp_path):
    import tomatocat.proactive.engine as module

    monkeypatch.setattr(module, "_STATE_ID_LIMIT", 2)
    engine = ProactiveEngine(
        tmp_path, FakeMCP(),
        memory=type("M", (), {"get_context_block": lambda self: ""})(),
        llm_call_fn=lambda *_: None,
        send_fn=lambda *_: None,
    )
    engine._seen_ids.update({"old", "middle", "new"})
    engine._seen_at.update({"old": 1.0, "middle": 2.0, "new": 3.0})
    engine._save_state()
    assert engine._seen_ids == {"middle", "new"}
