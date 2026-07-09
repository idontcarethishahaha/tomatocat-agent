"""🍅🐱 番茄猫 TomatoCat - 像素猫 AI 桌面助手

用法:
  python main.py                     启动番茄猫（默认，和 v2 一致）
  python main.py --desktop           启动桌面宠物模式
  python main.py --workspace DIR     指定工作目录
  python main.py --config PATH       指定配置文件
"""

from __future__ import annotations

import asyncio
import logging
import signal
import sys
import os
import threading
from pathlib import Path

ROOT = Path(__file__).parent
CONFIG_FILE = ROOT / "config.toml"
WORKSPACE_DIR = ROOT / "workspace"
PLUGINS_DIR = ROOT / "plugins"

sys.path.insert(0, str(ROOT))

_LOG_FORMAT = "%(asctime)s  %(levelname)-8s  %(name)s  %(message)s"
_LOG_DATEFMT = "%H:%M:%S"

def _setup_logging(workspace: Path | None = None) -> None:
    """配置日志：输出到控制台 + 工作区日志文件"""
    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stdout)]

    if workspace is not None:
        log_dir = workspace / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(
            log_dir / "bot.log",
            encoding="utf-8",
        )
        file_handler.setFormatter(logging.Formatter(_LOG_FORMAT, datefmt=_LOG_DATEFMT))
        handlers.append(file_handler)

    logging.basicConfig(
        level=logging.INFO,
        format=_LOG_FORMAT,
        datefmt=_LOG_DATEFMT,
        handlers=handlers,
        force=True,
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("telegram").setLevel(logging.WARNING)
    logging.getLogger("chromadb").setLevel(logging.WARNING)


_setup_logging()
logger = logging.getLogger("tomatocat")


async def serve(config_path: Path, workspace: Path) -> dict:
    from tomatocat.config import Config
    from tomatocat.bus import EventBus
    from tomatocat.session import SessionManager
    from tomatocat.plugins.manager import PluginManager
    from tomatocat.agent.agent import TomatoCatAgent
    from tomatocat.channels.cli_socket import CLISocketChannel
    from tomatocat.channels.telegram import TelegramChannel
    from tomatocat.channels.qq import QQChannel
    from tomatocat.checkpoint import CheckpointManager
    from tomatocat.plugins.default_memory.plugin import DefaultMemoryPlugin, MemoryPluginBuildDeps
    from tomatocat.mcp_client import MCPClient
    from tomatocat.meme import MemeService
    from tomatocat.proactive.engine import ProactiveEngine
    from tomatocat.scheduler import SchedulerService

    print("\n🍅🐱 番茄猫 TomatoCat 启动中...")
    print(f"   配置文件: {config_path}")
    print(f"   工作目录: {workspace}")
    print("")

    config = Config.load(config_path)
    workspace.mkdir(parents=True, exist_ok=True)

    event_bus = EventBus()
    session_manager = SessionManager(workspace=workspace)

    checkpoint_manager = CheckpointManager(workspace=workspace)
    logger.info("检查点管理器已就绪")

    llm_provider = None
    from tomatocat.agent.llm import LLMProvider
    if config.llm_main.model:
        llm_provider = LLMProvider(
            model=config.llm_main.model,
            api_key=config.llm_main.api_key,
            base_url=config.llm_main.base_url,
        )

    memory_plugin = DefaultMemoryPlugin()
    memory_runtime = memory_plugin.build(
        MemoryPluginBuildDeps(
            workspace=workspace,
            config=config,
            llm_provider=llm_provider or _create_dummy_llm(config),
            event_bus=event_bus,
        )
    )
    memory = memory_runtime.engine
    logger.info(f"记忆系统已就绪: {memory.describe().name}")

    mcp_client = MCPClient(
        workspace=workspace,
        config_file=config.mcp.config_file,
    )
    if config.mcp.enabled:
        mcp_tools = await mcp_client.start()
        logger.info(f"MCP 已连接，{len(mcp_tools)} 个工具可用")

    meme_service = None
    if config.meme.enabled:
        meme_dir = workspace / config.meme.meme_dir
        meme_service = MemeService(meme_dir)
        logger.info("Meme 服务已就绪")

    plugin_manager = PluginManager(
        plugins_dir=PLUGINS_DIR,
        workspace=workspace,
        event_bus=event_bus,
        session_manager=session_manager,
        memory=memory,
    )

    subagent_manager = None
    if llm_provider and config.llm_fast.model:
        from tomatocat.agent.background.subagent_manager import SubagentManager
        from tomatocat.agent.policies.delegation import DelegationPolicyPlugin

        fast_provider = LLMProvider(
            model=config.llm_fast.model,
            api_key=config.llm_fast.api_key,
            base_url=config.llm_fast.base_url,
        )
        subagent_manager = SubagentManager(
            provider=fast_provider,
            workspace=workspace,
            event_bus=event_bus,
            model=config.llm_fast.model,
            max_tokens=8192,
            plugin_manager=plugin_manager,
        )
        plugin_manager.context["subagent_manager"] = subagent_manager
        logger.info("子 Agent 管理器已就绪")

        delegation_policy = DelegationPolicyPlugin()
        plugin_manager.context["delegation_policy"] = delegation_policy
        logger.info("策略委托系统已就绪")

    await plugin_manager.load_all()

    if config.mcp.enabled:
        plugin_manager.register_mcp_tools(mcp_client)
        logger.info(f"插件加载完成: {len(plugin_manager._tools)} 个工具")

    from tomatocat.agent.skills import SkillsLoader
    skills_loader = SkillsLoader(workspace=workspace)
    logger.info(f"技能加载器已就绪，{len(skills_loader.list_skills())} 个技能可用")

    agent = TomatoCatAgent(
        config=config,
        workspace=workspace,
        event_bus=event_bus,
        session_manager=session_manager,
        plugin_manager=plugin_manager,
        memory=memory,
        meme_service=meme_service,
        skills_loader=skills_loader,
    )

    if memory:
        logger.info("[memory] 检测到未整合的 PENDING 记忆，启动时整合...")
        # 不阻塞启动流程，异步执行记忆整合
        async def _safe_consolidate():
            try:
                await memory.consolidate()
            except Exception as e:
                logger.warning(f"[memory] 启动时整合失败: {e}")
        asyncio.create_task(_safe_consolidate(), name="memory_consolidate")

    channels: list = []

    if config.channels.cli.enabled:
        host, port_str = config.channels.cli.socket.split(":")
        cli_channel = CLISocketChannel(host=host, port=int(port_str))
        channels.append(cli_channel)

    if config.channels.telegram.enabled and config.channels.telegram.token:
        tg_channel = TelegramChannel(
            token=config.channels.telegram.token,
            allow_from=config.channels.telegram.allow_from,
            upload_dir=workspace / "uploads",
        )
        channels.append(tg_channel)

    if config.channels.qq.enabled and config.channels.qq.bot_uin:
        qq_channel = QQChannel(
            bot_uin=config.channels.qq.bot_uin,
            allow_from=config.channels.qq.allow_from,
            groups=config.channels.qq.groups,
            upload_dir=workspace / "uploads",
        )
        channels.append(qq_channel)

    async def message_handler(session_key: str, text: str, channel: str, **kwargs) -> dict:
        return await agent.handle_message(session_key, text, channel, **kwargs)

    for ch in channels:
        ch.set_handler(message_handler)

    for ch in channels:
        try:
            await ch.start()
            logger.info("[channel] %s 已启动", ch.__class__.__name__)
        except Exception as e:
            logger.error("[channel] %s 启动失败: %s（该渠道不可用，其他渠道不受影响）",
                         ch.__class__.__name__, e)

    async def send_to_channel(channel_name: str, chat_id: str, message: str) -> None:
        for ch in channels:
            if ch.__class__.__name__.lower().startswith(channel_name.lower()):
                if hasattr(ch, "send_message"):
                    await ch.send_message(chat_id, message)
                return

    proactive = None
    if config.proactive.enabled and config.mcp.enabled:
        async def llm_call_wrapper(prompt: str) -> str:
            return await agent.llm.simple_chat(prompt)

        proactive = ProactiveEngine(
            workspace=workspace,
            mcp=mcp_client,
            memory=memory,
            llm_call_fn=llm_call_wrapper,
            send_fn=send_to_channel,
            poll_interval=config.proactive.poll_interval_seconds,
            target_channel=config.proactive.target.channel,
            target_chat_id=config.proactive.target.chat_id,
        )
        proactive.start()
        plugin_manager.proactive = proactive
        logger.info(f"主动推送已启动，目标: {config.proactive.target.channel}")

    scheduler = None
    scheduler_plugin = None
    if config.scheduler.enabled:
        default_channel = config.scheduler.default_channel or config.proactive.target.channel
        default_chat_id = config.scheduler.default_chat_id or config.proactive.target.chat_id

        async def scheduler_agent_fn(
            content: str,
            channel: str,
            chat_id: str,
            session_key: str,
        ) -> str:
            result = await agent.handle_message(session_key, content, channel)
            if isinstance(result, dict):
                return result.get("text", "")
            return str(result)

        scheduler = SchedulerService(
            store_path=workspace / "scheduler" / "jobs.json",
            send_fn=send_to_channel,
            agent_fn=scheduler_agent_fn,
            default_tz=config.scheduler.timezone,
            checkpoint_manager=checkpoint_manager,
        )
        await scheduler.start()

        scheduler_plugin_inst = plugin_manager.plugins.get("scheduler")
        if scheduler_plugin_inst:
            scheduler_plugin = scheduler_plugin_inst
            scheduler_plugin.set_scheduler(scheduler)
            scheduler_plugin.set_default_target(default_channel, default_chat_id)
            scheduler_plugin.set_timezone(config.scheduler.timezone)
            logger.info("定时任务插件已关联，默认目标: %s:%s", default_channel, default_chat_id)

        logger.info("定时任务服务已启动，时区: %s", config.scheduler.timezone)

    from tomatocat.dashboard_api import DashboardAPI
    dashboard = DashboardAPI(
        workspace=workspace,
        memory=memory,
        skills_loader=skills_loader,
        scheduler=scheduler,
        host="127.0.0.1",
        port=8765,
    )
    dashboard.start()

    print("\n🍅🐱 番茄猫已启动喵~ (≧∇≦)ﾉ\n")

    return {
        "agent": agent,
        "config": config,
        "plugin_manager": plugin_manager,
        "memory": memory,
        "meme_service": meme_service,
        "channels": channels,
        "proactive": proactive,
        "scheduler": scheduler,
        "mcp_client": mcp_client,
        "send_to_channel": send_to_channel,
        "stop_event": asyncio.Event(),
    }


async def _serve_forever(config_path: Path, workspace: Path) -> None:
    """默认模式：和 v2 完全一致的启动方式"""
    ctx = await serve(config_path, workspace)
    stop_event = ctx["stop_event"]
    loop = asyncio.get_running_loop()

    def _signal_handler() -> None:
        logger.info("收到退出信号，正在关闭...")
        stop_event.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _signal_handler)
        except NotImplementedError:
            signal.signal(sig, lambda *_: loop.call_soon_threadsafe(_signal_handler))

    try:
        await stop_event.wait()
    finally:
        if ctx["proactive"]:
            await ctx["proactive"].stop()
        if ctx["scheduler"]:
            await ctx["scheduler"].stop()
        for ch in ctx["channels"]:
            try:
                await ch.stop()
            except Exception as e:
                logger.error("渠道关闭失败: %s", e)
        await ctx["plugin_manager"].unload_all()
        await ctx["mcp_client"].close()
        print("\n🍅🐱 番茄猫下线了，晚安~ (=￣ω￣=)")


def _run_desktop(config_path: Path, workspace: Path) -> None:
    """桌面宠物模式：主线程 Qt，子线程 asyncio agent"""
    import time
    from error_log import log_startup, log_shutdown, log_error, set_log_path
    from config_mgr import set_config_path

    # 把桌面相关的状态文件都放到 workspace 下的 pet/ 子目录
    pet_dir = workspace / "pet"
    pet_dir.mkdir(parents=True, exist_ok=True)
    set_log_path(pet_dir / "pet-log.txt")
    set_config_path(pet_dir / "config.json")
    from chat_dialog import set_chat_log_path
    set_chat_log_path(pet_dir / "chat-history.json")

    agent_context = {}
    agent_ready = threading.Event()
    agent_loop = None

    def _agent_thread():
        nonlocal agent_loop

        async def _run():
            nonlocal agent_context
            ctx = await serve(config_path, workspace)
            agent_context.update(ctx)
            agent_ready.set()
            await ctx["stop_event"].wait()

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        agent_loop = loop
        try:
            loop.run_until_complete(_run())
        except BaseException as e:
            import traceback
            tb = traceback.format_exc()
            print(f"[Agent 线程崩溃] {e}\n{tb}", flush=True)
            logger.error(f"Agent 线程异常: {e}\n{tb}")
            for h in logging.getLogger().handlers:
                try:
                    h.flush()
                except Exception:
                    pass
            agent_ready.set()

    agent_thread = threading.Thread(target=_agent_thread, daemon=True)
    agent_thread.start()

    agent_ready.wait(timeout=60)
    if not agent_ready.is_set() or not agent_context:
        print("❌ Agent 启动失败，请检查日志")
        # 打印已有的 agent_context 信息帮助诊断
        if agent_context:
            print(f"   agent_context 有内容但可能不完整: {list(agent_context.keys())}")
        else:
            print("   agent_context 为空，serve() 可能崩溃了")
        return

    from PyQt6.QtWidgets import QApplication, QMessageBox
    from PyQt6.QtCore import Qt, QTimer

    _mutex_handle = None
    _MUTEX_NAME = "Global\\TomatoCatDesktopPet"

    def _acquire_lock():
        nonlocal _mutex_handle
        if sys.platform != "win32":
            _lock_path = ROOT / ".pet-lock"
            if _lock_path.exists():
                try:
                    old_pid = int(_lock_path.read_text().strip())
                    import ctypes
                    kernel32 = ctypes.windll.kernel32
                    handle = kernel32.OpenProcess(0x0400, False, old_pid)
                    if handle:
                        kernel32.CloseHandle(handle)
                        return False
                except:
                    pass
            _lock_path.write_text(str(os.getpid()))
            return True

        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.windll.kernel32
        CreateMutexW = kernel32.CreateMutexW
        CreateMutexW.argtypes = [wintypes.LPCVOID, wintypes.BOOL, wintypes.LPCWSTR]
        CreateMutexW.restype = wintypes.HANDLE

        WaitForSingleObject = kernel32.WaitForSingleObject
        WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        WaitForSingleObject.restype = wintypes.DWORD

        handle = CreateMutexW(None, False, _MUTEX_NAME)
        if not handle:
            return False

        WAIT_OBJECT_0 = 0
        WAIT_TIMEOUT = 0x102
        result = WaitForSingleObject(handle, 0)

        if result == WAIT_OBJECT_0:
            _mutex_handle = handle
            return True
        else:
            kernel32.CloseHandle(handle)
            return False

    def _release_lock():
        nonlocal _mutex_handle
        if sys.platform != "win32":
            _lock_path = ROOT / ".pet-lock"
            try:
                _lock_path.unlink()
            except:
                pass
            return

        if _mutex_handle:
            import ctypes
            kernel32 = ctypes.windll.kernel32
            try:
                kernel32.ReleaseMutex(_mutex_handle)
            except:
                pass
            try:
                kernel32.CloseHandle(_mutex_handle)
            except:
                pass
            _mutex_handle = None

    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )
    app = QApplication(sys.argv)
    app.setApplicationName("TomatoCat")
    app.setQuitOnLastWindowClosed(False)

    if not _acquire_lock():
        QMessageBox.information(None, "番茄猫",
                                "番茄猫已经在运行中啦~ 看看系统托盘？")
        sys.exit(0)

    from pet_window import PetWindow

    start_time = time.time()
    log_startup()

    exit_code = 0
    try:
        pet = PetWindow(agent_context=agent_context, agent_loop=agent_loop, workspace=str(workspace))
        pet.show()

        def health_check():
            if not pet.isVisible():
                log_error("Health check: window not visible, re-showing")
                pet.show()
                pet.raise_()
        QTimer.singleShot(3000, health_check)

        hour = time.localtime().tm_hour
        if 23 <= hour or hour < 6:
            pet.sys._enter_sleep()

        cb_timer = QTimer(pet)
        cb_timer.timeout.connect(pet.check_clipboard)
        cb_timer.start(2000)

        def proactive_loop():
            if not pet.sys.is_sleeping and pet.sys.mood < 30:
                pet.sys.show_bubble("有点寂寞...来跟我聊聊天喵~")

        mood_timer = QTimer(pet)
        mood_timer.timeout.connect(proactive_loop)
        mood_timer.start(120000)

        exit_code = app.exec()

    except Exception as e:
        log_error("Fatal crash", exc_info=True)
        exit_code = 1
    finally:
        log_shutdown()
        _release_lock()
        if agent_loop:
            agent_loop.call_soon_threadsafe(agent_context["stop_event"].set)

    runtime = time.time() - start_time
    if runtime < 10 and exit_code != 0:
        log_error(f"Watchdog restart (runtime={runtime:.1f}s)")
        time.sleep(1)
        import subprocess
        subprocess.Popen([sys.executable, __file__, "--desktop"])

    sys.exit(exit_code)


def _get_flag_value(args: list[str], flag: str) -> str | None:
    try:
        idx = args.index(flag)
    except ValueError:
        return None
    if idx + 1 >= len(args):
        raise ValueError(f"参数 {flag} 缺少值")
    return args[idx + 1]


def _has_flag(args: list[str], flag: str) -> bool:
    return flag in args


def main() -> None:
    args = sys.argv[1:]
    config_path = CONFIG_FILE
    workspace = WORKSPACE_DIR

    try:
        config_value = _get_flag_value(args, "--config")
        workspace_value = _get_flag_value(args, "--workspace")
    except ValueError as exc:
        print(str(exc))
        sys.exit(1)

    if config_value:
        config_path = Path(config_value)
    if workspace_value:
        workspace = Path(workspace_value)

    # 重新配置日志，输出到工作区
    _setup_logging(workspace)
    log_file = workspace / "logs" / "bot.log"
    logger.info("日志已配置，输出到: %s", log_file)

    if _has_flag(args, "--desktop"):
        _run_desktop(config_path, workspace)
    else:
        asyncio.run(_serve_forever(config_path, workspace))


if __name__ == "__main__":
    main()