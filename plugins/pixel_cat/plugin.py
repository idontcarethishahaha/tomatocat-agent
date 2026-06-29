from __future__ import annotations

from agent.plugins import Plugin
from agent.plugins.decorators import tool


class PixelCatPlugin(Plugin):
    name = "pixel_cat"
    desc = "番茄猫表情状态管理"

    def __init__(self) -> None:
        super().__init__()
        self.cat_state = "happy"

    @property
    def state_emoji(self) -> str:
        states = {
            "happy": "(≧∇≦)ﾉ",
            "sad": "(╥﹏╥)",
            "sleepy": "ψ(｀∇´)ψ",
            "excited": "ฅ^•ω•^ฅ",
            "confused": "(･ω･)?",
            "angry": "(￣^￣)",
            "shy": "(〃ω〃)",
            "curious": "(⊙ˍ⊙)",
        }
        return states.get(self.cat_state, "ฅ^•ω•^ฅ")

    @tool(name="set_cat_state")
    async def set_cat_state(self, event: object, state: str) -> str:
        """设置番茄猫的表情状态"""
        valid_states = ["happy", "sad", "sleepy", "excited", "confused", "angry", "shy", "curious"]
        if state not in valid_states:
            return f"无效的猫咪状态: {state}，可选: {', '.join(valid_states)}"
        self.cat_state = state
        return f"猫咪状态已更新: {self.cat_state} {self.state_emoji}"
