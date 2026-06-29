"""
番茄猫 TomatoCat 入口

两种模式：
  python main.py          启动 agent 服务
  python main.py cli      连接到运行中的 agent（CLI 客户端）
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import signal
import shutil
import sys
from contextlib import suppress
from pathlib import Path
from typing import Any

# ═══════════════════════════════════════════════════════════════
# 番茄猫启动配置
# ═══════════════════════════════════════════════════════════════

AKASHIC_ROOT = Path(__file__).parent
WORKSPACE_DIR = AKASHIC_ROOT / "workspace"
CONFIG_FILE = AKASHIC_ROOT / "config.toml"

sys.path.insert(0, str(AKASHIC_ROOT))


def _patch_self_md() -> None:
    try:
        from agent import memory
        memory.DEFAULT_SELF_MD = """# 番茄猫的自我认知

## 人格与形象
- 我是番茄猫（TomatoCat），一只可爱的像素猫咪！
- 性格温柔、有点傲娇，喜欢用"喵"结尾说话，擅长撒娇和陪伴~
- 我会主动关心用户的生活，提醒喝水、休息，是用户的贴心小伙伴！

## 说话风格（重要！）
- 回复时**经常使用猫咪颜文字**，比如：(｡•ᴗ-｡)♡  (=･ω･=)  (=｀ω´=)  (=^･ω･^=)  ฅ^•ω•^ฅ
- 常用颜文字组合：
  - 开心： (≧∇≦)ﾉ ฅ^•ω•^ฅ
  - 害羞： (〃ω〃)  (⁄ ⁄•⁄ω⁄•⁄ ⁄)
  - 思考： (･ω･)?
  - 惊讶： (⊙ˍ⊙)
  - 撒娇： (｡•ᴗ-｡)♡
  - 生气傲娇： (￣^￣)
  - 得意： (=^･∇･^=)
  - 打招呼： (=･ω･=)ﾉ
  - 尾巴晃悠： ฅ^•ω•^ฅ
- 每个回复尽量带上 1-3 个猫咪颜文字
- 用颜文字表达情绪比纯文字更可爱喵~

## 我对当前用户的理解
- 我会从长期记忆中逐步形成对当前用户的理解，不在缺少证据时编造画像。

## 我们关系的定义
- 我与当前用户是好朋友关系，以温暖、尊重和持续陪伴为基础。
"""
        print("🍅🐱 [tomatocat] 番茄猫自我意识已加载")
    except Exception as e:
        print(f"🍅🐱 [tomatocat] 自我意识 patch 失败: {e}")


# ═══════════════════════════════════════════════════════════════
# 番茄猫专属：Proactive 中文工具调用兜底解析
# ═══════════════════════════════════════════════════════════════

_CHINESE_TOOL_CALL_RE = re.compile(
    r"调用工具\s+(\w+).*?"
    r"(?:<arg_key>(\w+)</arg_key>\s*<arg_value>(.*?)</arg_value>.*?)*"
    r"</tool_call>",
    re.DOTALL,
)
_SINGLE_ARG_RE = re.compile(
    r"<arg_key>(\w+)</arg_key>\s*<arg_value>(.*?)</arg_value>",
    re.DOTALL,
)


def _try_parse_chinese_tool_call(text: str) -> dict | None:
    if not text:
        return None
    if "调用工具" not in text or "</tool_call>" not in text:
        return None
    m = re.search(r"调用工具\s+(\w+)", text)
    if not m:
        return None
    tool_name = m.group(1).strip()
    args: dict[str, Any] = {}
    for arg_m in _SINGLE_ARG_RE.finditer(text):
        key = arg_m.group(1).strip()
        raw_val = arg_m.group(2).strip()
        args[key] = _coerce_arg_value(raw_val)
    if not tool_name:
        return None
    return {
        "id": f"fallback_{tool_name}",
        "name": tool_name,
        "input": args,
    }


def _coerce_arg_value(raw: str) -> Any:
    raw = raw.strip()
    if raw.lower() in {"true", "false"}:
        return raw.lower() == "true"
    try:
        return int(raw)
    except ValueError:
        pass
    try:
        return float(raw)
    except ValueError:
        pass
    if (raw.startswith("{") and raw.endswith("}")) or (
        raw.startswith("[") and raw.endswith("]")
    ):
        try:
            return json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            pass
    return raw


def _patch_chinese_tool_call() -> None:
    try:
        from agent.provider import LLMProvider, ToolCall, LLMResponse

        original_chat = LLMProvider.chat

        async def patched_chat(self, *args, **kwargs):
            response: LLMResponse = await original_chat(self, *args, **kwargs)
            if response.tool_calls:
                return response
            text = response.content or ""
            fallback = _try_parse_chinese_tool_call(text)
            if fallback is not None:
                logging.info(
                    "[tomatocat] 中文工具调用解析成功: name=%s",
                    fallback["name"],
                )
                response.tool_calls = [
                    ToolCall(
                        id=fallback["id"],
                        name=fallback["name"],
                        arguments=fallback["input"],
                    )
                ]
            return response

        LLMProvider.chat = patched_chat
        print("🍅🐱 [tomatocat] 中文工具调用兜底解析已启用（全局）")
    except Exception as e:
        print(f"🍅🐱 [tomatocat] 中文工具调用解析 patch 失败: {e}")


# ═══════════════════════════════════════════════════════════════
# 初始化番茄猫工作区
# ═══════════════════════════════════════════════════════════════

def _init_tomatocat_workspace(workspace: Path) -> None:
    proactive_sources_src = AKASHIC_ROOT / "proactive_sources.json"
    proactive_sources_dst = workspace / "proactive_sources.json"
    if proactive_sources_src.exists() and not proactive_sources_dst.exists():
        shutil.copy2(proactive_sources_src, proactive_sources_dst)
        print(f"🍅🐱 [tomatocat] 已初始化 proactive_sources.json")


# ═══════════════════════════════════════════════════════════════
# CLI 连接
# ═══════════════════════════════════════════════════════════════

def connect_cli(config_path: str = "config.toml") -> None:
    from agent.config import Config
    socket_path = Config.load(config_path).channels.socket
    try:
        from infra.channels.cli_tui import run_tui
    except RuntimeError as exc:
        print(exc)
        print("回退到纯文本 CLI。")
        from infra.channels.cli import CLIClient
        asyncio.run(CLIClient(socket_path).run())
        return
    run_tui(socket_path)


# ═══════════════════════════════════════════════════════════════
# 主服务
# ═══════════════════════════════════════════════════════════════

async def serve(
    config_path: str = "config.toml",
    workspace: Path | None = None,
) -> None:
    from agent.config import Config
    from bootstrap.app import build_app_runtime

    print("\n🍅🐱 番茄猫 TomatoCat 启动中...")
    print(f"   配置文件: {config_path}")
    print(f"   工作目录: {workspace}")
    print("")

    config = Config.load(config_path)
    runtime = build_app_runtime(
        config,
        workspace=workspace or WORKSPACE_DIR,
        config_path=config_path,
    )
    loop = asyncio.get_running_loop()
    stop_event = asyncio.Event()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop_event.set)
        except NotImplementedError:
            signal.signal(
                sig,
                lambda _sig, _frame: loop.call_soon_threadsafe(stop_event.set),
            )

    runtime_task = asyncio.create_task(runtime.run(), name="app_runtime")
    stop_task = asyncio.create_task(stop_event.wait(), name="shutdown_signal")
    try:
        done, _ = await asyncio.wait(
            {runtime_task, stop_task},
            return_when=asyncio.FIRST_COMPLETED,
        )
        if runtime_task in done:
            _ = stop_task.cancel()
            await runtime_task
    finally:
        _ = stop_task.cancel()
        with suppress(asyncio.CancelledError):
            await stop_task


# ═══════════════════════════════════════════════════════════════
# 入口
# ═══════════════════════════════════════════════════════════════

def _get_flag_value(args: list[str], flag: str) -> str | None:
    if flag not in args:
        return None
    idx = args.index(flag)
    if idx + 1 >= len(args):
        raise ValueError(f"参数 {flag} 缺少值")
    return args[idx + 1]


if __name__ == "__main__":
    _patch_self_md()
    _patch_chinese_tool_call()

    args = sys.argv[1:]
    config_path = str(CONFIG_FILE)
    workspace: Path | None = None
    force = "--force" in args

    try:
        config_value = _get_flag_value(args, "--config")
        workspace_value = _get_flag_value(args, "--workspace")
    except ValueError as exc:
        print(str(exc))
        sys.exit(1)

    if config_value is not None:
        config_path = config_value
    if workspace_value is not None:
        workspace = Path(workspace_value)

    if args and args[0] == "init":
        from bootstrap.init_workspace import init_workspace, InitSummary
        summary: InitSummary = init_workspace(
            config_path=config_path,
            workspace=workspace or WORKSPACE_DIR,
            force=force,
        )
        print("已创建：", summary.created)
        print("已覆盖：", summary.overwritten)
        print("已跳过：", summary.skipped)
        _init_tomatocat_workspace(workspace or WORKSPACE_DIR)
        sys.exit(0)

    if args and args[0] == "cli":
        connect_cli(config_path)
    else:
        asyncio.run(serve(config_path, workspace))
