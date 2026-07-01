"""像素猫插件 - 控制番茄猫的表情和状态"""

from __future__ import annotations

from tomatocat.plugins import Plugin, tool


class PixelCatPlugin(Plugin):
    name = "pixel_cat"
    desc = "番茄猫表情和状态控制"

    def __init__(self) -> None:
        super().__init__()
        self._cat_state = "idle"
        self._cat_mood = "happy"

    @tool(name="set_cat_state", description="设置番茄猫的状态和表情")
    async def set_cat_state(
        self,
        event: object,
        state: str = "idle",
        mood: str = "happy",
    ) -> str:
        """
        设置番茄猫的状态和心情

        Args:
            state: 猫咪状态（idle/happy/sad/sleeping/thinking/working）
            mood: 心情（happy/sad/excited/sleepy/angry/curious）
        """
        self._cat_state = state
        self._cat_mood = mood

        faces = {
            "happy": "(≧∇≦)ﾉ",
            "sad": "(・_・;)",
            "excited": "ヽ(=^･ω･^=)丿",
            "sleepy": "(=￣ω￣=)",
            "angry": "(=｀ω´=)",
            "curious": "(｡•ᴗ-｡)♡",
        }
        face = faces.get(mood, "(=^･ω･^=)")

        return f"番茄猫状态更新：{state} {face}"
