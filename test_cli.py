"""Manual CLI socket smoke test.

Run explicitly with ``uv run python test_cli.py`` after starting TomatoCat.
It is intentionally not collected by pytest because it talks to a live socket.
"""

import asyncio
import json
import os


async def run_chat() -> None:
    host = os.getenv("TOMATOCAT_CLI_HOST", "127.0.0.1")
    port = int(os.getenv("TOMATOCAT_CLI_PORT", "8771"))
    reader, writer = await asyncio.open_connection(host, port)

    messages = [
        "你好，番茄猫！",
        "帮我记录一笔支出，50元，餐饮，午饭",
        "看看本月收支",
    ]
    for msg in messages:
        print(f"\n用户: {msg}")
        writer.write((json.dumps({"text": msg}, ensure_ascii=False) + "\n").encode("utf-8"))
        await writer.drain()
        data = await reader.readline()
        if not data:
            break
        response = json.loads(data.decode("utf-8"))
        print(f"番茄猫: {response.get('content', '')}")

    writer.close()
    await writer.wait_closed()


if __name__ == "__main__":
    asyncio.run(run_chat())
