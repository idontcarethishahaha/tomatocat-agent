import asyncio
import json


async def test_chat():
    reader, writer = await asyncio.open_connection("127.0.0.1", 8770)

    messages = [
        "你好呀番茄猫！",
        "帮我记录一笔支出，50元，餐饮，午饭",
        "看看本月收支",
    ]

    for msg in messages:
        print(f"\n👤 用户: {msg}")
        writer.write((json.dumps({"text": msg}, ensure_ascii=False) + "\n").encode("utf-8"))
        await writer.drain()

        data = await reader.readline()
        if not data:
            break
        response = json.loads(data.decode("utf-8"))
        print(f"🍅🐱 番茄猫: {response['content']}")

    writer.close()
    await writer.wait_closed()


asyncio.run(test_chat())
