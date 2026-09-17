from tomatocat.agent.agent import (
    _is_simple_message,
    _should_search_memory,
    _simple_reply,
    _tools_for_message,
)


def test_simple_greetings_are_fast_path_candidates():
    for text in ("hello", " Hello! ", "你好", "晚安~", "谢谢"):
        assert _is_simple_message(text)
        assert not _should_search_memory(text)


def test_memory_references_always_search_memory():
    for text in ("你还记得我吗", "继续刚才的计划", "按照我们之前说的"):
        assert _should_search_memory(text)


def test_uncertain_messages_are_conservative():
    assert not _is_simple_message("我最近有点累")
    assert _should_search_memory("我最近有点累")


def test_simple_reply_ignores_trailing_punctuation():
    assert _simple_reply("hello!") == "喵~ 你好呀！"
    assert _simple_reply("好的。") == "好哒，收到喵~"


def test_desktop_tool_routing_is_conservative():
    tools = [{"type": "function", "function": {"name": "weather"}}]
    assert _tools_for_message(tools, "北京今天冷不冷", "desktop") == tools
    assert _tools_for_message(tools, "陪我聊聊天", "desktop") == []
    assert _tools_for_message(tools, "陪我聊聊天", "telegram") == tools
